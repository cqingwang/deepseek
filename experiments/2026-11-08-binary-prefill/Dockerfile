# Thin candidate image: the promoted runtime and native extensions stay byte
# identical; only the selected vLLM/TileLang Python sources are replaced.
ARG BASE_IMAGE=spark-ds41f:1030
FROM ${BASE_IMAGE}

ARG CANDIDATE_SOURCE_SHA256=unknown

COPY overlay/vllm/models/deepseek_v4_1/sp_prefill.py \
     /opt/spark-ds41f/candidate/vllm/vllm/models/deepseek_v4_1/sp_prefill.py

LABEL local.spark-ds41f.experiment.name="binary-prefill-20261108" \
      local.spark-ds41f.experiment.source-sha256="${CANDIDATE_SOURCE_SHA256}" \
      local.spark-ds41f.experiment.base-image="spark-ds41f:1030"
