ARG branch=latest
FROM dizcza/docker-hashcat:${branch}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG DEBIAN_FRONTEND=noninteractive

ENV APP_ROOT=/opt/hashcat-wpa-server \
    HASHCAT_WPA_APP_ROOT=/opt/hashcat-wpa-server \
    HASHCAT_WPA_INSTALL_ROOT=/opt/hashcat-wpa-server \
    HASHCAT_WPA_SERVER_HOME=/data \
    HASHCAT_WPA_DATA_DIR=/data \
    HASHCAT_WPA_LOG_DIR=/var/log/hashcat-wpa-server \
    HOME=/data \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    PYTHONPATH=/opt/hashcat-wpa-server \
    PATH=/opt/hashcat-wpa-server/venv/bin:$PATH \
    LOG_CONSOLE=1 \
    POTFILE_DISABLE=0

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        apt-utils \
        bash \
        bzip2 \
        ca-certificates \
        clinfo \
        curl \
        hcxtools \
        nginx \
        ocl-icd-libopencl1 \
        pciutils \
        pocl-opencl-icd \
        procps \
        python3 \
        python3-pip \
        python3-venv \
        sudo \
        supervisor \
        wget && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --system --no-create-home --shell /usr/sbin/nologin nginx 2>/dev/null || true

WORKDIR ${APP_ROOT}

COPY ./requirements.txt ${APP_ROOT}/requirements.txt
RUN python3 -m venv "${APP_ROOT}/venv" && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r "${APP_ROOT}/requirements.txt"

COPY . ${APP_ROOT}
COPY ./supervisor.conf /etc/supervisor/conf.d/hashcat_wpa.conf
COPY ./nginx.conf /etc/nginx/nginx.conf
COPY ./docker/entrypoint.sh /usr/local/bin/hashcat-wpa-entrypoint

RUN chmod +x /usr/local/bin/hashcat-wpa-entrypoint && \
    (chmod +x "${APP_ROOT}"/bash/*.sh "${APP_ROOT}"/bash/crackserver || true)

RUN mkdir -p \
        "${APP_ROOT}/logs/supervisor" \
        "${APP_ROOT}/wordlists" \
        "${APP_ROOT}/rules" \
        "${HASHCAT_WPA_SERVER_HOME}/brain" \
        "${HASHCAT_WPA_SERVER_HOME}/captures" \
        "${HASHCAT_WPA_SERVER_HOME}/database" \
        "${HASHCAT_WPA_SERVER_HOME}/recovery" \
        "${HASHCAT_WPA_SERVER_HOME}/wordlists" \
        "${HASHCAT_WPA_LOG_DIR}/supervisor" && \
    for dict in Top1575-probable-v2.txt Top304Thousand-probable-v2.txt; do \
        if [ ! -s "${APP_ROOT}/wordlists/${dict}" ]; then \
            wget -q --no-check-certificate \
                "https://github.com/berzerk0/Probable-Wordlists/raw/master/Real-Passwords/${dict}" \
                -O "${APP_ROOT}/wordlists/${dict}" || rm -f "${APP_ROOT}/wordlists/${dict}"; \
        fi; \
    done && \
    if command -v kwp >/dev/null 2>&1 && [ -d /root/kwprocessor ]; then \
        : > "${APP_ROOT}/wordlists/keyboard-walk.txt"; \
        for keymap in /root/kwprocessor/keymaps/*; do \
            [ -e "$keymap" ] || continue; \
            kwp /root/kwprocessor/basechars/tiny.base "$keymap" \
                /root/kwprocessor/routes/2-to-16-max-3-direction-changes.route \
                >> "${APP_ROOT}/wordlists/keyboard-walk.txt" || true; \
        done; \
    fi && \
    if command -v hashcat >/dev/null 2>&1 && \
        [ -s "${APP_ROOT}/wordlists/Top1575-probable-v2.txt" ] && \
        [ -s "${APP_ROOT}/rules/best64.rule" ]; then \
        hashcat --stdout --rules="${APP_ROOT}/rules/best64.rule" \
            "${APP_ROOT}/wordlists/Top1575-probable-v2.txt" | \
            sort -u > "${APP_ROOT}/wordlists/Top1575-probable-v2-rule-best64.txt" || true; \
    fi && \
    (wget -q --no-check-certificate \
        https://github.com/Unic0rn28/hashcat-rules/raw/main/unicorn%20rules/UnicornLorge.rule \
        -O "${APP_ROOT}/rules/UnicornLorge.rule" || true) && \
    (wget -q --no-check-certificate \
        https://github.com/stealthsploit/OneRuleToRuleThemStill/raw/main/OneRuleToRuleThemStill.rule \
        -O "${APP_ROOT}/rules/OneRuleToRuleThemStill.rule" || true) && \
    find "${APP_ROOT}/wordlists" -type f -exec chmod a-wx,u+rw {} \; && \
    chmod -R a+rX "${APP_ROOT}" && \
    chmod 700 "${HASHCAT_WPA_SERVER_HOME}"

VOLUME ["/data", "/var/log/hashcat-wpa-server"]
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

ENTRYPOINT ["hashcat-wpa-entrypoint"]
CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
