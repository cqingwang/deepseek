ARG BASE_IMAGE=spark-ds41f:x1118-raw-official
FROM ${BASE_IMAGE}

COPY vllm/vllm/entrypoints/openai/responses/utils.py \
     /opt/spark-ds41f/candidate/vllm/vllm/entrypoints/openai/responses/utils.py
COPY vllm/tests/entrypoints/openai/responses/test_responses_utils.py \
     /opt/spark-ds41f/candidate/vllm/tests/entrypoints/openai/responses/test_responses_utils.py

RUN rm -f /opt/spark-ds41f/candidate/vllm/vllm/entrypoints/openai/responses/__pycache__/utils*.pyc

LABEL local.spark-ds41f.vllm.patch-head="6fb28658e1fd1d6132af25cf4525482f23797ea9"
LABEL local.spark-ds41f.vllm.tree="bb5439cabc793d79b50829c1fe10429389f0eed0"
