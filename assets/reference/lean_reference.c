#include <math.h>
#include <stddef.h>
#include "lean_policy_export.h"
#define POLICY_N_H1 256
#define POLICY_N_H2 128
#define POLICY_N_OUT 4
typedef void (*policy_service_fn)(void);
static inline float elu(float x) {
  return x > 0.0f ? x : expm1f(x);
}

/* Stable softplus avoids float overflow and matches Torch to float precision. */
static inline float softplus(float x) {
  const float ax = fabsf(x);
  return (x > 0.0f ? x : 0.0f) + log1pf(expf(-ax));
}

/* Four accumulators hide VFMA latency and reduce float32 summation error. */
/* restrict lets the compiler retain inputs in registers. */
/* Process four rows per input load while preserving each row's reduction order. */
static void linear(const float *restrict w, const float *restrict b, const float *restrict in,
                   float *restrict out, int rows, int cols, policy_service_fn service) {
  if (rows <= 0 || cols <= 0)
    return;
  const int grouped_rows = rows - rows % 4;
  const int grouped_cols = cols - cols % 4;
  int r0 = 0;
  for (; r0 < grouped_rows; r0 += 4) {
    const float *wa = w + (size_t)(r0 + 0) * cols;
    const float *wb = w + (size_t)(r0 + 1) * cols;
    const float *wc = w + (size_t)(r0 + 2) * cols;
    const float *wd = w + (size_t)(r0 + 3) * cols;
    float a0 = 0.0f, a1 = 0.0f, a2 = 0.0f, a3 = 0.0f;
    float b0 = 0.0f, b1 = 0.0f, b2 = 0.0f, b3 = 0.0f;
    float c0 = 0.0f, c1 = 0.0f, c2 = 0.0f, c3 = 0.0f;
    float d0 = 0.0f, d1 = 0.0f, d2 = 0.0f, d3 = 0.0f;
    int c = 0;
    for (; c < grouped_cols; c += 4) {
      const float x0 = in[c + 0], x1 = in[c + 1];
      const float x2 = in[c + 2], x3 = in[c + 3];
      a0 += wa[c + 0] * x0;
      a1 += wa[c + 1] * x1;
      a2 += wa[c + 2] * x2;
      a3 += wa[c + 3] * x3;
      b0 += wb[c + 0] * x0;
      b1 += wb[c + 1] * x1;
      b2 += wb[c + 2] * x2;
      b3 += wb[c + 3] * x3;
      c0 += wc[c + 0] * x0;
      c1 += wc[c + 1] * x1;
      c2 += wc[c + 2] * x2;
      c3 += wc[c + 3] * x3;
      d0 += wd[c + 0] * x0;
      d1 += wd[c + 1] * x1;
      d2 += wd[c + 2] * x2;
      d3 += wd[c + 3] * x3;
    }
    for (; c < cols; ++c) {
      const float x = in[c];
      a0 += wa[c] * x;
      b0 += wb[c] * x;
      c0 += wc[c] * x;
      d0 += wd[c] * x;
    }
    out[r0 + 0] = ((a0 + a1) + (a2 + a3)) + b[r0 + 0];
    out[r0 + 1] = ((b0 + b1) + (b2 + b3)) + b[r0 + 1];
    out[r0 + 2] = ((c0 + c1) + (c2 + c3)) + b[r0 + 2];
    out[r0 + 3] = ((d0 + d1) + (d2 + d3)) + b[r0 + 3];
    if (service != NULL)
      service();
  }

  for (int r = r0; r < rows; ++r) {
    const float *wr = w + (size_t)r * cols;
    float a0 = 0.0f, a1 = 0.0f, a2 = 0.0f, a3 = 0.0f;
    int c = 0;
    for (; c < grouped_cols; c += 4) {
      a0 += wr[c + 0] * in[c + 0];
      a1 += wr[c + 1] * in[c + 1];
      a2 += wr[c + 2] * in[c + 2];
      a3 += wr[c + 3] * in[c + 3];
    }
    for (; c < cols; ++c)
      a0 += wr[c] * in[c]; /* Remaining input columns. */
    out[r] = ((a0 + a1) + (a2 + a3)) + b[r];
  }
}

/* Share the forward pass across weight sets to keep both policy heads identical. */
static void forward(const float *w0, const float *b0, const float *w1, const float *b1,
                    const float *w2, const float *b2, int n_in, int n_h1, int n_h2,
                    const float *obs, float *act, policy_service_fn service) {
  /* Static activations keep stack use bounded; policy inference is not reentrant. */
  static float h1[POLICY_N_H1];
  static float h2[POLICY_N_H2];
  static float out[POLICY_N_OUT];

  linear(w0, b0, obs, h1, n_h1, n_in, service);
  for (int i = 0; i < n_h1; ++i) {
    h1[i] = elu(h1[i]);
    if ((i & 7) == 7 && service != NULL)
      service();
  }

  linear(w1, b1, h1, h2, n_h2, n_h1, service);
  for (int i = 0; i < n_h2; ++i) {
    h2[i] = elu(h2[i]);
    if ((i & 7) == 7 && service != NULL)
      service();
  }

  /* No activation on the head -- these are the raw Beta parameters. */
  linear(w2, b2, h2, out, POLICY_N_OUT, n_h2, service);

  /* Head order is alpha_raw then beta_raw; output is the Beta mean scaled to [-1, 1]. */
  for (int i = 0; i < 2; ++i) {
    const float alpha = softplus(out[i]) + 1.0f;
    const float beta = softplus(out[i + 2]) + 1.0f;
    act[i] = (alpha / (alpha + beta)) * 2.0f - 1.0f;
  }
}

void policy_forward_lean(const float *obs, float *act, policy_service_fn service) {
  forward(LEAN_W0, LEAN_B0, LEAN_W1, LEAN_B1, LEAN_W2, LEAN_B2, POLICY_LEAN_N_IN, POLICY_LEAN_N_H1,
          POLICY_LEAN_N_H2, obs, act, service);
}
