"""Candidate image smoke: import the overlaid source and verify its env gate."""

from vllm.models.deepseek_v4_1 import sp_prefill
from vllm.models.deepseek_v4_1.tilelang import moe

expected = (int(__import__("os").environ["EXPECTED_MOE"]),
            int(__import__("os").environ["EXPECTED_WO"]),
            int(__import__("os").environ["EXPECTED_BLOCK"]))
actual = (sp_prefill.MOE_SLICE_TOKENS, sp_prefill.WO_SLICE_TOKENS, moe._prefill_block_m())
assert actual == expected, (actual, expected)
print(f"candidate env smoke passed: MOE={actual[0]} WO={actual[1]} block_M={actual[2]}")
