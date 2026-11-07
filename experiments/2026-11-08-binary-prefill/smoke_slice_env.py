"""Candidate image smoke: import the overlaid source and verify its env gate."""

from vllm.models.deepseek_v4_1 import sp_prefill

expected = (int(__import__("os").environ["EXPECTED_MOE"]),
            int(__import__("os").environ["EXPECTED_WO"]))
actual = (sp_prefill.MOE_SLICE_TOKENS, sp_prefill.WO_SLICE_TOKENS)
assert actual == expected, (actual, expected)
print(f"slice env smoke passed: MOE={actual[0]} WO={actual[1]}")
