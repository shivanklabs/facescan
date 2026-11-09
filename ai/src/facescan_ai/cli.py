"""facescan-ai: offline face pipeline on the command line (CPU only).

    uv run --env-file .env facescan-ai index <photos_dir> --event <name>
    uv run --env-file .env facescan-ai search <selfie.jpg> --event <name> --top 20
    uv run facescan-ai blur <photo> <out>
    uv run facescan-ai clean --event <name>

The selfie and its embedding exist only in memory: they are never written to disk or logs.
Index files are AES-256-GCM encrypted with FACESCAN_BENCH_KEY (see index_store.py).
"""

import time
from pathlib import Path
from typing import Annotated

import cv2
import typer

from facescan_ai.config import DetectorConfig
from facescan_ai.image_io import ImageDecodeError, list_images, read_image
from facescan_ai.index_store import (
    EventIndex,
    IndexedFace,
    IndexStoreError,
    delete_index,
    index_path,
    load_index,
    load_key,
    save_index,
    validate_event_name,
    work_dir,
)
from facescan_ai.model_registry import ModelRegistry, ModelRegistryError
from facescan_ai.pipeline import PRIMARY_ENGINE, SECOND_ENGINE, FacePipeline, NoFaceFoundError
from facescan_ai.privacy.face_blur import blur_faces
from facescan_ai.search import all_finite, rank_photos

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="FaceScan offline face pipeline (benchmark use; CPU only).",
)

EventOption = Annotated[str, typer.Option("--event", help="Event name: [a-z0-9][a-z0-9_-]*")]
MinFaceOption = Annotated[
    float, typer.Option("--min-face", min=4.0, help="Minimum face size in pixels (short side).")
]
ScoreOption = Annotated[
    float, typer.Option("--score", min=0.0, max=1.0, help="Minimum detector score.")
]
ThreadsOption = Annotated[
    int, typer.Option("--threads", min=0, help="ONNX Runtime threads (0 = automatic).")
]
_DEFAULTS = DetectorConfig()


def _fail(message: str) -> typer.Exit:
    """Purpose: print an error (no traceback, no data) and return an exit to raise."""
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=1)


def _event(name: str) -> str:
    """Purpose: validate the event name or exit."""
    try:
        return validate_event_name(name)
    except ValueError as exc:
        raise _fail(str(exc)) from exc


def _pipeline(min_face: float, score: float, threads: int) -> FacePipeline:
    """Purpose: load verified models or exit with a readable error. Side effects: reads models."""
    try:
        config = DetectorConfig(min_face_px=min_face, score_threshold=score)
        return FacePipeline(ModelRegistry(), config, threads=threads)
    except (ModelRegistryError, ValueError) as exc:
        raise _fail(str(exc)) from exc


@app.command()
def index(
    photos_dir: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True)
    ],
    event: EventOption,
    min_face: MinFaceOption = _DEFAULTS.min_face_px,
    score: ScoreOption = _DEFAULTS.score_threshold,
    threads: ThreadsOption = 0,
    recursive: Annotated[bool, typer.Option(help="Include sub-folders.")] = True,
) -> None:
    """
    Detect, align, score and embed every face in a folder; save the event's encrypted index.

    Purpose: build (or rebuild) one event's index.
    Inputs: photo folder, event name, detector settings.
    Outputs: summary on stdout (faces, photos/s, failures).
    Side effects: reads photos and models; writes ai/benchmark/work/<event>.fsidx.
    """
    event = _event(event)
    try:
        key = load_key()
    except IndexStoreError as exc:
        raise _fail(str(exc)) from exc
    pipeline = _pipeline(min_face, score, threads)
    photos = list_images(photos_dir, recursive=recursive)
    if not photos:
        raise _fail("no .jpg/.jpeg/.png/.webp photos found")
    result = EventIndex(event=event, engine_versions=pipeline.engine_versions)
    failures: list[str] = []
    started = time.perf_counter()
    for path in photos:
        photo_id = path.relative_to(photos_dir).as_posix()
        try:
            image = read_image(path)
        except (ImageDecodeError, OSError) as exc:
            failures.append(f"{photo_id}: {exc}")
            continue
        result.photos.append(photo_id)
        for analysed in pipeline.analyse(image):
            result.faces.append(
                IndexedFace(
                    photo_id=photo_id,
                    bbox=analysed.face.bbox,
                    landmarks=analysed.face.landmarks,
                    detector_score=round(analysed.face.score, 4),
                    quality=analysed.quality.value,
                    embeddings={k: e.vector for k, e in analysed.embeddings.items()},
                )
            )
    elapsed = time.perf_counter() - started
    target = save_index(result, key, work_dir())
    processed = len(result.photos)
    typer.echo(f"event:            {event}")
    typer.echo(f"photos processed: {processed} of {len(photos)}")
    typer.echo(f"faces found:      {len(result.faces)}")
    typer.echo(f"failures:         {len(failures)}")
    for failure in failures:
        typer.echo(f"  - {failure}")
    typer.echo(f"time:             {elapsed:.2f} s")
    typer.echo(f"photos/second:    {processed / elapsed if elapsed > 0 else 0.0:.2f}")
    typer.echo(f"engines:          {result.engine_versions}")
    typer.echo(f"index:            {target}")


@app.command()
def search(
    selfie: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    event: EventOption,
    top: Annotated[int, typer.Option("--top", min=1, max=1000)] = 20,
    min_face: MinFaceOption = _DEFAULTS.min_face_px,
    score: ScoreOption = _DEFAULTS.score_threshold,
    threads: ThreadsOption = 0,
) -> None:
    """
    Search one event with a selfie (raw debug scores; release bands come in milestone 2).

    Purpose: rank the event's photos by similarity to the largest face in the selfie.
    Inputs: selfie path, event name, number of results.
    Outputs: photo ids with AuraFace and SFace cosine scores on stdout.
    Side effects: reads the selfie into memory and the encrypted index; writes nothing.
    """
    event = _event(event)
    try:
        key = load_key()
        event_index = load_index(event, key, work_dir())
    except IndexStoreError as exc:
        raise _fail(str(exc)) from exc
    pipeline = _pipeline(min_face, score, threads)
    try:
        image = read_image(selfie)
        analysed, faces_seen = pipeline.analyse_selfie(image)
    except (ImageDecodeError, OSError, NoFaceFoundError) as exc:
        raise _fail(f"selfie: {exc}") from exc
    del image  # selfie pixels are not needed after embedding
    query = {k: e.vector for k, e in analysed.embeddings.items()}
    if not all_finite(query):
        raise _fail("selfie embedding is invalid")
    stored = {k for k in event_index.engine_versions if k in (PRIMARY_ENGINE, SECOND_ENGINE)}
    current = {e.engine: e.version for e in pipeline.embedders}
    for engine in stored:
        if event_index.engine_versions[engine] != current.get(engine):
            raise _fail(f"index was built with another {engine} version; re-run index")
    matches = rank_photos(event_index, query, PRIMARY_ENGINE, SECOND_ENGINE, top)
    if faces_seen > 1:
        typer.echo(f"note: {faces_seen} faces in the selfie; using the largest one")
    typer.echo(f"selfie quality: {analysed.quality.value:.3f} {analysed.quality.components}")
    typer.echo(
        f"event {event}: {len(event_index.photos)} photos, {len(event_index.faces)} faces "
        "(raw scores, not release decisions)"
    )
    typer.echo(f"{'rank':>4}  {PRIMARY_ENGINE:>8}  {SECOND_ENGINE:>6}  {'quality':>7}  photo")
    for rank, match in enumerate(matches, start=1):
        typer.echo(
            f"{rank:>4}  {match.primary_score:>8.4f}  {match.second_score:>6.4f}  "
            f"{match.face_quality:>7.3f}  {match.photo_id}"
        )


@app.command()
def blur(
    photo: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    out: Annotated[Path, typer.Argument(dir_okay=False)],
    threads: ThreadsOption = 0,
) -> None:
    """
    Blur every face (permissive detector, generous margin) and write a new image.

    Purpose: privacy blur. Inputs: photo, output path (.jpg/.png/.webp; must not exist).
    Outputs: summary line. Side effects: writes `out` (no EXIF is copied).
    """
    if out.exists():
        raise _fail("output file already exists")
    if out.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise _fail("output must be .jpg, .jpeg, .png or .webp")
    if out.resolve() == photo.resolve():
        raise _fail("output must differ from the input")
    pipeline = _pipeline(_DEFAULTS.min_face_px, _DEFAULTS.score_threshold, threads)
    try:
        image = read_image(photo)
    except (ImageDecodeError, OSError) as exc:
        raise _fail(f"photo: {exc}") from exc
    faces = pipeline.blur_detector.detect(image)
    ok, encoded = cv2.imencode(out.suffix.lower(), blur_faces(image, faces))
    if not ok:
        raise _fail("could not encode the output image")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("xb") as fh:
        fh.write(encoded.tobytes())
    typer.echo(f"blurred {len(faces)} face(s) -> {out}")


@app.command()
def clean(event: EventOption) -> None:
    """
    Delete an event's index file.

    Purpose: remove benchmark data for one event. Side effects: deletes one file.
    """
    event = _event(event)
    directory = work_dir()
    if delete_index(event, directory):
        typer.echo(f"deleted {index_path(event, directory)}")
    else:
        typer.echo(f"no index for event '{event}'")


def main() -> None:
    """Purpose: console-script entry point (`facescan-ai`)."""
    app()


if __name__ == "__main__":
    main()
