// SPDX-License-Identifier: LicenseRef-CASU-AntiCapitalist-1.4
// Shared real-FFT analysis for the visualizer — direct port of
// casu/waveform.py live_fft/window_wave (2048-sample Hann window ending at
// the playhead, rfft magnitudes max-normalized to frequencyBinCount bins;
// oscilloscope window of the most recent 45 ms downsampled to 2048 points).
// Iterative radix-2 Cooley–Tukey matches numpy.fft.rfft for real input.
#pragma once
#include <QVector>

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstddef>
#include <vector>

namespace mpcasu::viz {

constexpr int kFftSize = 2048;
constexpr int kFftBins = 1024;   // frequencyBinCount parity
constexpr int kWavePoints = 2048;
constexpr double kWelchWindowS = 0.045;

inline void fft_in_place(std::vector<std::complex<double>>* a) {
    const std::size_t n = a->size();
    if (n < 2) return;
    for (std::size_t i = 1, j = 0; i < n; ++i) {
        std::size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap((*a)[i], (*a)[j]);
    }
    for (std::size_t len = 2; len <= n; len <<= 1) {
        const double angle = -2.0 * M_PI / static_cast<double>(len);
        const std::complex<double> wlen(std::cos(angle), std::sin(angle));
        for (std::size_t i = 0; i < n; i += len) {
            std::complex<double> w(1.0, 0.0);
            for (std::size_t k = 0; k < len / 2; ++k) {
                const std::complex<double> u = (*a)[i + k];
                const std::complex<double> v =
                    (*a)[i + k + len / 2] * w;
                (*a)[i + k] = u + v;
                (*a)[i + k + len / 2] = u - v;
                w *= wlen;
            }
        }
    }
}

// live_fft(): bars[b] in [0,1], b<kFftBins.
inline QVector<double> live_fft_bins(const float* pcm, std::size_t count,
                                     double position_s) {
    QVector<double> out(kFftBins, 0.0);
    if (!pcm || count == 0) return out;
    qint64 centre = static_cast<qint64>(position_s * 44100.0);
    centre = std::clamp<qint64>(centre, 0, static_cast<qint64>(count) - 1);
    const qint64 start =
        std::max<qint64>(0, centre - static_cast<qint64>(kFftSize));
    const qint64 end = centre;
    if (end - start < 64) return out;
    const std::size_t usable = static_cast<std::size_t>(end - start);
    std::vector<std::complex<double>> buf(kFftSize, {0.0, 0.0});
    double mean = 0.0;
    for (std::size_t i = 0; i < usable; ++i)
        mean += pcm[static_cast<std::size_t>(start) + i];
    mean /= static_cast<double>(usable);
    for (std::size_t i = 0; i < usable; ++i) {
        const double hann =
            0.5 * (1.0 - std::cos(2.0 * M_PI * static_cast<double>(i) /
                                  static_cast<double>(usable - 1)));
        buf[i] = std::complex<double>(
            (pcm[static_cast<std::size_t>(start) + i] - mean) * hann, 0.0);
    }
    fft_in_place(&buf);
    double maximum = 0.0;
    for (int b = 0; b < kFftBins; ++b) {
        const double mag = std::abs(buf[static_cast<std::size_t>(b)]);
        out[b] = mag;
        if (mag > maximum) maximum = mag;
    }
    if (maximum > 0.0)
        for (double& v : out) v /= maximum;
    return out;
}

}  // namespace mpcasu::viz
