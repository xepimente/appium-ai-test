FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04

ENV DEBIAN_FRONTEND=noninteractive

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Java (required by Appium)
    openjdk-17-jdk \
    # ADB client — connects to host's ADB daemon via TCP
    android-tools-adb \
    # OCR engine for pytesseract
    tesseract-ocr \
    # OpenCV system dependencies
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    # Python
    python3 \
    python3-pip \
    python3-dev \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 20 ─────────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Java env ───────────────────────────────────────────────────────────────────
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH=${JAVA_HOME}/bin:${PATH}

# ── Android env ────────────────────────────────────────────────────────────────
# Create a minimal ANDROID_HOME so Appium can locate the adb binary via the
# standard SDK layout (platform-tools/adb), without installing the full SDK.
ENV ANDROID_HOME=/opt/android-sdk
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV PATH=${ANDROID_HOME}/platform-tools:${PATH}

RUN mkdir -p ${ANDROID_HOME}/platform-tools \
    && ln -s /usr/bin/adb ${ANDROID_HOME}/platform-tools/adb

# ADB client in the container talks to the ADB server running on the host.
# The host manages the USB connection to the physical device.
# On Windows/macOS: host.docker.internal resolves automatically.
# On Linux: devcontainer.json passes --add-host to wire it up.
ENV ANDROID_ADB_SERVER_ADDRESS=host.docker.internal
ENV ANDROID_ADB_SERVER_PORT=5037

# ── Appium 2 + UiAutomator2 driver ────────────────────────────────────────────
# APPIUM_HOME must be a shared, world-readable path so all users (root during
# build, vscode at runtime) find the installed driver in the same place.
ENV APPIUM_HOME=/usr/local/share/appium
RUN npm install -g appium@2.17.1 \
    && appium driver install uiautomator2@3.7.8 \
    && chmod -R 777 ${APPIUM_HOME}

# ── Python default ─────────────────────────────────────────────────────────────
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# ── Python dependencies ─────────────────────────────────────────────────────────
# Install at image build time so the correct versions are always present.
# post-create.sh also runs pip install to pick up any local changes.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir --upgrade --ignore-installed -r /tmp/requirements.txt
