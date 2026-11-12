#!/usr/bin/env bash
# Removes exactly what mesh-up.sh creates.
set -uo pipefail
systemctl stop 'spark-ring-mesh-marker@*' 2>/dev/null || true
tc filter del dev enp1s0f0np0 ingress pref 49152 protocol 0x88b5 handle 49152 flower 2>/dev/null || true
tc filter del dev enp1s0f1np1 ingress pref 49162 protocol 0x88b5 handle 49162 flower 2>/dev/null || true
[ -z "$(tc filter show dev enp1s0f0np0 ingress; tc filter show dev enp1s0f0np0 egress)" ] && tc qdisc del dev enp1s0f0np0 clsact 2>/dev/null || true
[ -z "$(tc filter show dev enp1s0f1np1 ingress; tc filter show dev enp1s0f1np1 egress)" ] && tc qdisc del dev enp1s0f1np1 clsact 2>/dev/null || true
ip route del 10.251.20.10/32 2>/dev/null || true
ip route del 10.100.241.13/32 2>/dev/null || true
echo mesh-down done
