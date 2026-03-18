# 🎵 DIY Shazam

> Identify any song from your **microphone** or an **audio file** — powered by FFT analysis and the AudD API.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-22c55e?style=flat-square)
![API](https://img.shields.io/badge/AudD-Free_Tier-f59e0b?style=flat-square)
![Status](https://img.shields.io/badge/Status-Active-22c55e?style=flat-square)

---

## 📌 What Is This?

DIY Shazam is a lightweight Python app that:

1. Records audio from your **microphone** or loads an **audio file**
2. Runs **FFT (Fast Fourier Transform)** analysis and plots the frequency spectrum
3. Sends the clip to the **AudD API** for song recognition
4. Displays the **song title, artist**, and **album art** in your terminal

Built as a learning project to understand how music recognition works under the hood.

---

## 🧠 How It Works

```
Run main.py
    │
    ▼
Choose: 🎤 Microphone  OR  📁 Audio File
    │
    ▼
Capture 8-second WAV clip
    │
    ▼
FFT Analysis → frequency spectrum saved as fft_output.png
    │
    ▼
POST audio to AudD API
    │
    ▼
🎵 Song name · Artist · Album art displayed in terminal
```

---

## 🛠 Tech Stack

| Layer | Tool |
|---|---|
| Language | Python 3.10+ |
| Mic Recording | `sounddevice` |
| FFT Analysis | `scipy.fft` + `numpy` |
| FFT Visualisation | `matplotlib` |
| Song Matching | AudD API (free tier) |
| Image Display | `Pillow` + `requests` |
| UI | Terminal |

---

## 📁 Project Structure

```
diy-shazam/
├── main.py             # Entry point — mic or file mode
├── recorder.py         # Record from mic OR load audio file
├── fft_analyze.py      # Run FFT, plot frequency spectrum
├── matcher.py          # Send audio to AudD API, get result
├── display.py          # Print song info, show album art
├── requirements.txt
└── README.md
```

---

## ⚡ Setup & Run

### 1. Clone the repo

```bash
git clone https://github.com/icecold009/diy-shazam.git
cd diy-shazam
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Get your free AudD API key

- Go to [https://audd.io/](https://audd.io/) and sign up for free
- You get **100 recognitions/day** on the free tier
- Copy your API token

### 4. Create a `.env` file

```env
AUDD_API_TOKEN=your_token_here
```

> ⚠️ Never push your `.env` file to GitHub. It's already in `.gitignore`.

### 5. Run the app

```bash
python main.py
```

---

## 🖥 Sample Output

```
Listen via microphone or load a file? (mic/file): mic

🎙  Recording for 8 seconds...
✅  FFT analysis saved to fft_output.png

🎵  Song:    Blinding Lights
🎤  Artist:  The Weeknd

[Album art opens in image viewer]
```

---

## 📝 Notes

- Record in a **quiet environment** for best recognition results
- The FFT plot shows the **dominant frequencies** of your audio clip
- Tested on Windows · macOS · Linux (VSCode terminal)

---

## 📚 What I Learned

- How FFT decomposes audio into frequency components
- How music recognition APIs work under the hood
- Python audio recording and WAV file handling
- API calls with multipart file uploads
- Clean, modular project structure



MIT License free to use and modify.