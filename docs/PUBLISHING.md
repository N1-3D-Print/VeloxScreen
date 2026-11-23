# Publishing VeloxScreen on GitHub

This ZIP is **repository-ready**, but it has **not** been uploaded to a GitHub account for you.

## Using the GitHub website (simplest)

1. Create a new repository named `VeloxScreen`. Keep it empty (do not generate another README or LICENSE during creation).
2. Extract this ZIP locally and upload **the files inside the `VeloxScreen` folder**, preserving folders like `.github`, `assets`, `docs`, and `scripts`.
3. Confirm that the repository shows `README.md`, `main.py`, `LICENSE`, the `assets/` folder, and both issue templates.
4. Replace the example `YOUR_ACCOUNT` in README's clone command with your actual GitHub user or organization.
5. Turn on GitHub Issues if it is not already enabled.
6. Once tested on your Pi, create a Release using tag `v0.3.5-alpha`, mark it as **Pre-release**, and reiterate that this software is experimental.

## Using git

```bash
# In the extracted VeloxScreen folder
git init
git add .
git commit -m "Initial public alpha package"
git branch -M main
# Replace YOUR_ACCOUNT with the real GitHub owner and create the empty repo first.
git remote add origin https://github.com/YOUR_ACCOUNT/VeloxScreen.git
git push -u origin main
```

## Before publishing

Read [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). Do not include actual printer configs, API keys, passwords, personal logs, screenshots with private data or installed virtual environments.

The bundled splash image is project artwork. Before wider redistribution, check that you have the rights required for the intended logo use and trademark registrations in your target markets. Do not imply endorsement by Klipper or the upstream projects.
