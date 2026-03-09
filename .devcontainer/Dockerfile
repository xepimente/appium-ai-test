
# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Download the Android SDK (mirrors appium-docker-android exactly)
# Uses the same base, same SDK version, same sdkmanager commands.
# The resulting /opt/android directory is copied into stage 2.
# ══════════════════════════════════════════════════════════════════════════════
ARG UBUNTU_VERSION="noble-20260113"
FROM ubuntu:${UBUNTU_VERSION} AS android_sdk

RUN apt-get -qqy update && \
    apt dist-upgrade -y && \
    apt-get -qqy --no-install-recommends install \
    ca-certificates \
    openjdk-25-jdk \
    unzip \
    wget \
  && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME="/usr/lib/jvm/java-25-openjdk-amd64"
ENV PATH=$PATH:${JAVA_HOME}/bin

ENV SDK_VERSION=commandlinetools-linux-13114758_latest
ENV ANDROID_BUILD_TOOLS_VERSION=36.0.0
ENV ANDROID_CMD=cmdline-tools
ENV ANDROID_HOME=/opt/android
ENV ANDROID_CMD_HOME=/opt/android/${ANDROID_CMD}
ENV ANDROID_TOOL_HOME=${ANDROID_CMD_HOME}/tools
ENV PATH=$PATH:${ANDROID_TOOL_HOME}:${ANDROID_TOOL_HOME}/bin

RUN wget -O tools.zip https://dl.google.com/android/repository/${SDK_VERSION}.zip && \
    unzip tools.zip && rm tools.zip && \
    mkdir -p ${ANDROID_CMD_HOME}/tools && \
    mv ${ANDROID_CMD}/* ${ANDROID_TOOL_HOME}/ && \
    rm -rf ${ANDROID_CMD} && \
    echo y | sdkmanager "platform-tools" && \
    echo y | sdkmanager "build-tools;$ANDROID_BUILD_TOOLS_VERSION"


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — devcontainer runtime
# Copies the Android SDK from stage 1 so the container has the exact same
# official Google ADB binary that appium-docker-android uses.
# ══════════════════════════════════════════════════════════════════════════════
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

ENV DEBIAN_FRONTEND=noninteractive

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Java (required by Appium)
    openjdk-25-jdk \
    # Timezone data
    tzdata \
    # Virtual framebuffer — allows xvfb-run appium (same as appium-docker-android)
    xvfb \
    # OCR engine for pytesseract
    tesseract-ocr \
    # OpenCV system dependencies
    libgl1 \
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

# ── Timezone ───────────────────────────────────────────────────────────────────
ENV TZ="UTC"
RUN echo "${TZ}" > /etc/timezone \
  && dpkg-reconfigure --frontend noninteractive tzdata

# ── Node.js 24 ─────────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Java env ───────────────────────────────────────────────────────────────────
ENV JAVA_HOME=/usr/lib/jvm/java-25-openjdk-amd64
ENV PATH=${JAVA_HOME}/bin:${PATH}

# ── Android SDK (official Google SDK, copied from build stage) ─────────────────
# This is the same /opt/android directory that appium-docker-android produces.
# It contains:
#   platform-tools/adb  ← the real Google-built ADB binary (not the apt package)
#   cmdline-tools/tools ← sdkmanager, avdmanager
#   build-tools/        ← aapt, zipalign, etc.
ENV ANDROID_HOME=/opt/android
ENV ANDROID_SDK_ROOT=/opt/android
COPY --from=android_sdk ${ANDROID_HOME} ${ANDROID_HOME}
ENV PATH=${PATH}:\
${ANDROID_HOME}/cmdline-tools/tools:\
${ANDROID_HOME}/cmdline-tools/tools/bin:\
${ANDROID_HOME}/platform-tools:\
${ANDROID_HOME}/build-tools

# ── Appium 3 + UiAutomator2 driver ────────────────────────────────────────────
# APPIUM_HOME must be world-readable so all users (root at build, vscode at
# runtime) find the installed driver in the same location.
ENV APPIUM_HOME=/usr/local/share/appium
RUN npm install -g appium@3.2.0 \
    && appium driver install uiautomator2@6.7.13 \
    && chmod -R 777 ${APPIUM_HOME}

# ── Wireless ADB scripts (from appium-docker-android) ─────────────────────────
# Placed in /usr/local/bin so they are on PATH without needing APP_PATH.
COPY .devcontainer/wireless_connect.sh /usr/local/bin/wireless_connect.sh
COPY .devcontainer/wireless_autoconnect.sh /usr/local/bin/wireless_autoconnect.sh
RUN chmod +x /usr/local/bin/wireless_connect.sh /usr/local/bin/wireless_autoconnect.sh

# ── Python default ─────────────────────────────────────────────────────────────
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# ── Python dependencies ────────────────────────────────────────────────────────
# Installed at build time; post-create.sh also runs pip install to pick up
# any local changes made after the image was built.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir --upgrade --ignore-installed \
    --break-system-packages -r /tmp/requirements.txt
