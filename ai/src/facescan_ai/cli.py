"""facescan-ai: offline face pipeline and matching engine on the command line (CPU only).

    uv run --env-file .env facescan-ai index <photos_dir> --event <name>
    uv run --env-file .env facescan-ai match <selfie.jpg> --event <name>
    uv run --env-file .env facescan-ai search <selfie.jpg> --event <name> --top 20
    uv run --env-file .env facescan-ai label <event>
    uv run --env-file .env facescan-ai bench <event> [--synthetic]
    uv run facescan-ai blur <photo> <out>
    uv run facescan-ai clean --event <name>

A selfie must show exactly one face. The selfie and its embedding exist only in memory: they
are never written to disk or logs. Index files are AES-256-GCM encrypted with FACESCAN_BENCH_KEY.
"""

import sys
import time
from pathlib import Path
from typing import Annotated

import cv2
import typer

from facescan_ai.config import DetectorConfig
from facescan_ai.event_builder import EventBuildConfig, build_event
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
from facescan_ai.matching.matcher import MatchConfig, match_event
from facescan_ai.matching.quality_gate import QualityGateConfig
from facescan_ai.model_registry import ModelRegistry, ModelRegistryError
from facescan_ai.pipeline import (
    PRIMARY_ENGINE,
    SECOND_ENGINE,
    FacePipeline,
    SelfieFaceCountError,
    SelfieQuery,
)
from facescan_ai.privacy.face_blur import blur_faces
from facescan_ai.search import all_finite, rank_photos

BENCHMARK_DIR = Path(__file__).resolve().parents[2] / "benchmark"

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    help="FaceScan offline face pipeline and matching engine (benchmark use; CPU only).",
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
_GATE = QualityGateConfig()


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


def _load_event(event: str) -> EventIndex:
    """Purpose: read and decrypt an event index or exit. Side effects: reads the env and file."""
    try:
        return load_index(event, load_key(), work_dir())
    except IndexStoreError as exc:
        raise _fail(str(exc)) from exc


def _check_engines(index: EventIndex, pipeline: FacePipeline) -> None:
    """Purpose: refuse to compare embeddings from different model versions."""
    current = {e.engine: e.version for e in pipeline.embedders}
    for engine in (PRIMARY_ENGINE, SECOND_ENGINE):
        if index.engine_versions.get(engine) != current.get(engine):
            raise _fail(f"index was built with another {engine} version; re-run index")


def _selfie_query(pipeline: FacePipeline, selfie: Path) -> SelfieQuery:
    """
    Purpose: build the selfie query from one frame, enforcing the one-face rule.
    Side effects: reads the selfie into memory only. Exits on any selfie problem.
    """
    try:
        frames = [read_image(selfie)]
        query = pipeline.analyse_selfie_frames(frames, _GATE)
    except SelfieFaceCountError as exc:
        raise _fail(f"selfie refused: {exc}") from exc
    except (ImageDecodeError, OSError) as exc:
        raise _fail(f"selfie: {exc}") from exc
    del frames  # selfie pixels are not needed after embedding
    if not all_finite(query.vectors):
        raise _fail("selfie embedding is invalid")
    return query


def _use_benchmark_modules() -> None:
    """Purpose: make ai/benchmark importable (labeler, runner). Side effects: edits sys.path."""
    if str(BENCHMARK_DIR) not in sys.path:
        sys.path.insert(0, str(BENCHMARK_DIR))


@app.command()
def index(
    photos_dir: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True)
    ],
    event: EventOption,
    min_face: MinFaceOption = _DEFAULTS.min_face_px,
    score: ScoreOption = _DEFAULTS.score_threshold,
    weak_face_px: Annotated[
        float, typer.Option("--weak-face-px", min=0.0, help="Faces smaller than this are weak.")
    ] = _GATE.min_face_px,
    weak_quality: Annotated[
        float,
        typer.Option("--weak-quality", min=0.0, max=1.0, help="Faces below this quality are weak."),
    ] = _GATE.min_quality,
    threads: ThreadsOption = 0,
    recursive: Annotated[bool, typer.Option(help="Include sub-folders.")] = True,
) -> None:
    """
    Detect, embed, gate, calibrate and cluster every face in a folder; save the event index.

    Purpose: build (or rebuild) one event's matchable index.
    Inputs: photo folder, event name, detector and quality-gate settings.
    Outputs: summary on stdout (faces, clusters, calibration, time per photo).
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
    analyse_seconds = time.perf_counter() - started
    try:
        gate = QualityGateConfig(min_face_px=weak_face_px, min_quality=weak_quality)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    summary = build_event(result, EventBuildConfig(gate=gate))
    total = time.perf_counter() - started
    target = save_index(result, key, work_dir())
    processed = len(result.photos)
    per_photo = total / processed if processed else 0.0
    typer.echo(f"event:              {event}")
    typer.echo(f"photos processed:   {processed} of {len(photos)}")
    typer.echo(f"faces found:        {summary.faces} ({summary.weak_faces} weak)")
    typer.echo(
        f"clusters:           {summary.clusters} ({summary.single_face_clusters} single-face); "
        f"weak faces attached {summary.attached_weak}, unattached {summary.unclustered_weak}"
    )
    for line in summary.calibration_lines:
        typer.echo(f"calibration:        {line}")
    typer.echo(f"failures:           {len(failures)}")
    for failure in failures:
        typer.echo(f"  - {failure}")
    typer.echo(
        f"time:               {total:.2f} s (analysis {analyse_seconds:.2f} s, "
        f"gate+calibration+clustering {summary.seconds:.3f} s)"
    )
    typer.echo(f"time per photo:     {per_photo:.2f} s including clustering")
    typer.echo(f"engines:            {result.engine_versions}")
    typer.echo(f"index:              {target}")


@app.command()
def match(
    selfie: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    event: EventOption,
    margin: Annotated[
        float, typer.Option("--margin", min=0.0, max=0.99, help="Best-vs-second cluster margin.")
    ] = MatchConfig().min_margin,
    min_face: MinFaceOption = _DEFAULTS.min_face_px,
    score: ScoreOption = _DEFAULTS.score_threshold,
    threads: ThreadsOption = 0,
) -> None:
    """
    Match a single-face selfie against one event and print photo ids per band.

    Purpose: release decision (release band) with borderline listed as debug output.
    Side effects: reads the selfie into memory and the encrypted index; writes nothing.
    """
    event = _event(event)
    event_index = _load_event(event)
    pipeline = _pipeline(min_face, score, threads)
    _check_engines(event_index, pipeline)
    query = _selfie_query(pipeline, selfie)
    result = match_event(event_index, query.vectors, query.strong, MatchConfig(min_margin=margin))
    t = result.thresholds
    typer.echo(
        f"selfie: quality {query.quality.value:.3f}, "
        f"{'strong' if query.strong else 'WEAK (no release possible)'}"
    )
    for cal in event_index.calibration.values():
        typer.echo(f"calibration: {cal.describe()}")
    typer.echo(
        f"thresholds: {PRIMARY_ENGINE} release {t.primary_release:.3f} / borderline "
        f"{t.primary_borderline:.3f}; {SECOND_ENGINE} release {t.second_release:.3f}"
    )
    typer.echo(
        f"best cluster: {result.cluster_id} ({result.cluster_band}), "
        f"score {result.best_score:.3f}, "
        f"second {result.second_score:.3f}, margin {result.margin:.3f} "
        f"({'ok' if result.margin_ok else 'too small'})"
    )
    typer.echo(f"RELEASE ({len(result.release_photos)} photos):")
    for photo in result.release_photos:
        typer.echo(f"  {photo}")
    typer.echo(f"debug, borderline, not released ({len(result.borderline_photos)} photos):")
    for photo in result.borderline_photos:
        typer.echo(f"  {photo}")


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
    Raw debug ranking of an event's photos against a single-face selfie (no release rules).

    Purpose: inspect raw cosine scores. Use `match` for release decisions.
    Side effects: reads the selfie into memory and the encrypted index; writes nothing.
    """
    event = _event(event)
    event_index = _load_event(event)
    pipeline = _pipeline(min_face, score, threads)
    _check_engines(event_index, pipeline)
    query = _selfie_query(pipeline, selfie)
    matches = rank_photos(event_index, query.vectors, PRIMARY_ENGINE, SECOND_ENGINE, top)
    typer.echo(f"selfie quality: {query.quality.value:.3f} {query.quality.components}")
    typer.echo(
        f"event {event}: {len(event_index.photos)} photos, {len(event_index.faces)} faces "
        "(raw scores, not release decisions)"
    )
    typer.echo(f"{'rank':>4}  {PRIMARY_ENGINE:>8}  {SECOND_ENGINE:>6}  {'quality':>7}  photo")
    for rank, m in enumerate(matches, start=1):
        typer.echo(
            f"{rank:>4}  {m.primary_score:>8.4f}  {m.second_score:>6.4f}  "
            f"{m.face_quality:>7.3f}  {m.photo_id}"
        )


@app.command()
def label(
    event: Annotated[str, typer.Argument(help="Benchmark event in ai/benchmark/datasets/")],
    port: Annotated[int, typer.Option("--port", min=1024, max=65535)] = 8765,
) -> None:
    """
    Open the local labelling page for a benchmark event (http://127.0.0.1:<port>).

    Purpose: label clusters and faces with opaque person ids into labels.json.
    Side effects: serves on 127.0.0.1 only; writes datasets/<event>/labels.json.
    """
    event = _event(event)
    _use_benchmark_modules()
    from bench_dataset import LabelsError, dataset_paths, load_labels
    from labeler.app import LabelerState, create_app, serve

    paths = dataset_paths(event)
    if not paths.photos.is_dir():
        raise _fail(
            f"missing {paths.photos}; index it with: "
            f"facescan-ai index {paths.photos} --event {event}"
        )
    event_index = _load_event(event)
    try:
        labels = load_labels(paths.labels, event)
    except LabelsError as exc:
        raise _fail(str(exc)) from exc
    state = LabelerState(
        event_index, paths.photos, labels, paths.labels, sorted(paths.selfie_persons())
    )
    typer.echo(f"labeler for {event}: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    serve(create_app(state), port)


@app.command()
def bench(
    event: Annotated[str, typer.Argument(help="Benchmark event in ai/benchmark/datasets/")],
    synthetic: Annotated[
        bool, typer.Option("--synthetic", help="Use generated vectors instead of a dataset.")
    ] = False,
    margin: Annotated[
        float, typer.Option("--margin", min=0.0, max=0.99)
    ] = MatchConfig().min_margin,
    threads: ThreadsOption = 0,
) -> None:
    """
    Run the benchmark: match every person's selfie and score releases against labels.json.

    Purpose: precision, recall, false releases and the 95% upper bound, overall and per person.
    Side effects: writes reports to ai/benchmark/reports/ and per-photo debug to the work folder.
    """
    event = _event(event)
    _use_benchmark_modules()
    from bench_dataset import DEFAULT_REPORTS_DIR, LabelsError, dataset_paths, load_labels
    from run_benchmark import SelfieInput, evaluate, write_reports

    config = MatchConfig(min_margin=margin)
    if synthetic:
        from synthetic import make_synthetic_event

        generated = make_synthetic_event(event=event)
        summary = build_event(generated.index, EventBuildConfig())
        result = evaluate(generated.index, generated.labels, generated.selfies, config)
        prefix = f"synthetic_{event}"
        typer.echo(
            f"synthetic event: {len(generated.index.photos)} photos, {summary.faces} faces, "
            f"{summary.clusters} clusters, build {summary.seconds:.2f} s"
        )
    else:
        paths = dataset_paths(event)
        try:
            labels = load_labels(paths.labels, event)
        except LabelsError as exc:
            raise _fail(str(exc)) from exc
        if not labels.faces:
            raise _fail(f"no labels yet; run: facescan-ai label {event}")
        if not labels.complete_photos:
            raise _fail(
                "no photo is marked fully labelled; mark photos in the labeler (key c) first"
            )
        event_index = _load_event(event)
        pipeline = _pipeline(_DEFAULTS.min_face_px, _DEFAULTS.score_threshold, threads)
        _check_engines(event_index, pipeline)
        selfies: dict[str, SelfieInput] = {}
        for person_id, path in paths.selfie_persons().items():
            try:
                query = pipeline.analyse_selfie_frames([read_image(path)], _GATE)
            except (SelfieFaceCountError, ImageDecodeError, OSError) as exc:
                typer.echo(f"skip selfie {person_id}: {exc}")
                continue
            selfies[person_id] = SelfieInput(vectors=query.vectors, strong=query.strong)
        if not selfies:
            raise _fail(f"no usable selfies in {paths.selfies}")
        result = evaluate(event_index, labels, selfies, config)
        prefix = None
    md, js, dbg = write_reports(result, DEFAULT_REPORTS_DIR, work_dir(), prefix)
    t = result.total
    typer.echo(f"persons evaluated: {len(result.persons)}")
    typer.echo(
        f"photos counted (fully labelled): {result.photos_counted}; excluded: "
        f"{result.photos_excluded}; releases excluded: {result.releases_excluded}"
    )
    typer.echo(
        f"released {t.released}, true {t.true_releases}, false {t.false_releases}, "
        f"missed {t.missed}"
    )
    precision = "n/a" if t.precision is None else f"{100 * t.precision:.2f}%"
    recall = "n/a" if t.recall is None else f"{100 * t.recall:.2f}%"
    typer.echo(f"precision {precision}, recall {recall}")
    typer.echo(
        f"false-release rate 95% upper bound: {100 * result.upper_bound_95:.2f}% "
        f"(99.9% claim needs >= {result.releases_needed_for_999} releases with 0 false)"
    )
    typer.echo(f"reports: {md} and {js}")
    typer.echo(f"per-photo debug (git-ignored): {dbg}")


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
