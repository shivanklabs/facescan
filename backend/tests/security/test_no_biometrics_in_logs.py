"""Invariants 3 and 7: logs never contain images, vectors, OTPs, tokens or email addresses."""

import pytest


@pytest.mark.skip(reason="TODO: needs selfie search flow")
def test_selfie_search_leaves_no_biometrics_in_logs() -> None:
    # TODO: run a selfie search with caplog at DEBUG; assert no base64 image data, float arrays,
    # OTPs, tokens or emails appear, and nothing was written to disk or storage.
    pass
