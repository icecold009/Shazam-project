from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np


def analyze_audio(samples, sample_rate: int, output_path: str | Path = "fft_output.png") -> Path:
	"""Write a diagnostic FFT visualization; this does not identify songs."""
	array = np.asarray(samples, dtype=np.float32).reshape(-1)

	if array.size == 0:
		raise ValueError("Audio samples cannot be empty")

	if sample_rate <= 0:
		raise ValueError("sample_rate must be greater than zero")

	output_file = Path(output_path)
	output_file.parent.mkdir(parents=True, exist_ok=True)

	spectrum = np.fft.rfft(array)
	magnitudes = np.abs(spectrum)
	frequencies = np.fft.rfftfreq(array.size, d=1.0 / sample_rate)

	fig, ax = plt.subplots(figsize=(10, 4))
	ax.plot(frequencies, magnitudes, color="#0f766e", linewidth=1.0)
	ax.set_title("Frequency Spectrum")
	ax.set_xlabel("Frequency (Hz)")
	ax.set_ylabel("Magnitude")
	ax.grid(True, alpha=0.25)
	fig.tight_layout()
	fig.savefig(output_file, dpi=150)
	plt.close(fig)

	return output_file
