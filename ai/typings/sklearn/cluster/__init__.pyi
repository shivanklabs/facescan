# Minimal type stub for sklearn.cluster.HDBSCAN as used by facescan_ai.
import numpy as np
from numpy.typing import NDArray

class HDBSCAN:
    def __init__(
        self,
        min_cluster_size: int = 5,
        min_samples: int | None = None,
        cluster_selection_epsilon: float = 0.0,
        metric: str = "euclidean",
        cluster_selection_method: str = "eom",
        allow_single_cluster: bool = False,
        copy: bool = False,
    ) -> None: ...
    def fit_predict(self, x: NDArray[np.float64]) -> NDArray[np.int64]: ...
