#pragma once
#include <libpq-fe.h>

typedef struct {
    int   reachable;
    float avg_rtt_ms;
    float jitter_ms;
    float packet_loss_pct;
    float throughput_mbps;
    int   samples;
    char  method[64];
    char  note[512];
    char  steps[64];    /* e.g. "tcp icmp http udp" */
} ProbeResult;

void menu_probe(PGconn *conn);
