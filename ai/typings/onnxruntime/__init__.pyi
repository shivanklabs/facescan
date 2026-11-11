# Minimal type stub for the parts of onnxruntime used by facescan_ai (the package ships no
# type information). Keep it limited to what the code calls; extend when new APIs are used.
from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

class NodeArg:
    @property
    def name(self) -> str: ...
    @property
    def shape(self) -> list[int | str | None]: ...
    @property
    def type(self) -> str: ...

class SessionOptions:
    log_severity_level: int
    intra_op_num_threads: int
    def __init__(self) -> None: ...

class InferenceSession:
    def __init__(
        self,
        path_or_bytes: str | bytes,
        sess_options: SessionOptions | None = None,
        providers: Sequence[str] | None = None,
    ) -> None: ...
    def get_inputs(self) -> list[NodeArg]: ...
    def get_outputs(self) -> list[NodeArg]: ...
    def run(
        self,
        output_names: Sequence[str] | None,
        input_feed: dict[str, NDArray[np.float32] | NDArray[np.int64]],
    ) -> list[NDArray[np.float32]]: ...

def set_default_logger_severity(severity: int) -> None: ...
