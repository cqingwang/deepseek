# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

from types import SimpleNamespace

import torch

from vllm.v1.structured_output import StructuredOutputManager


class _FakeBackend:
    def __init__(self) -> None:
        self.max_rows: int | None = None

    def allocate_token_bitmask(self, max_rows: int) -> torch.Tensor:
        self.max_rows = max_rows
        return torch.full((max_rows, 1), -1, dtype=torch.int32)


class _FakeGrammar:
    def is_terminated(self) -> bool:
        return False

    def fill_bitmask(self, bitmask: torch.Tensor, index: int) -> None:
        if not 0 <= index < bitmask.shape[0]:
            raise RuntimeError(
                f"bitmask index {index} is outside {bitmask.shape[0]} rows"
            )

    def accept_tokens(self, request_id: str, tokens: list[int]) -> bool:
        return True

    def rollback(self, num_tokens: int) -> None:
        return None


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        structured_output_request=SimpleNamespace(grammar=_FakeGrammar()),
        use_structured_output=True,
        is_prefill_chunk=False,
    )


def test_copy_draft_bitmask_capacity_uses_full_decode_envelope() -> None:
    manager = object.__new__(StructuredOutputManager)
    manager.backend = _FakeBackend()
    manager._grammar_bitmask = None
    manager._full_mask = torch.tensor(-1, dtype=torch.int32)
    manager.fill_bitmask_parallel_threshold = 128
    manager.enable_in_reasoning = True
    manager.vllm_config = SimpleNamespace(
        num_speculative_tokens=5,
        max_decode_rows=41,
        scheduler_config=SimpleNamespace(max_num_seqs=5),
        model_config=SimpleNamespace(is_diffusion=False),
    )

    request_ids = ["copy-a", "copy-b", "uniform"]
    requests = {request_id: _request() for request_id in request_ids}
    scheduled_spec_decode_tokens = {
        "copy-a": list(range(16)),
        "copy-b": list(range(16)),
        "uniform": list(range(5)),
    }

    bitmask = manager.grammar_bitmask(
        requests,
        request_ids,
        scheduled_spec_decode_tokens,
        draft_zero_req_ids=None,
    )

    # 16 + 1 rows for each copy request, and 5 + 1 for the uniform request.
    assert bitmask is not None
    assert bitmask.shape[0] == 40
    assert manager.backend.max_rows == 41


if __name__ == "__main__":
    test_copy_draft_bitmask_capacity_uses_full_decode_envelope()
