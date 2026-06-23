# syntax=docker/dockerfile:1.7
FROM eclipse-temurin:8-jdk-jammy

ENV DEBIAN_FRONTEND=noninteractive \
    SYN_TREN_HOME=/opt/syntren \
    SYN_TREN_PATCHED_LIB=/opt/syntren-patched-lib \
    JAVA_BIN=/opt/java/openjdk/bin/java \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY wrappers/simulation_data_tools/simulators/syntren/repo/syntren1.2release/ /opt/syntren/

RUN test -f /opt/syntren/SynTReN.jar \
    && echo "1830a5d10c909b135040181b22470aac84ffb6f1ffc085f91089c92f728b8f5a  /opt/syntren/SynTReN.jar" | sha256sum -c -

RUN mkdir -p /opt/syntren-patched-lib \
    && curl -fsSL -o /opt/syntren-patched-lib/xstream-1.4.7.jar https://repo1.maven.org/maven2/com/thoughtworks/xstream/xstream/1.4.7/xstream-1.4.7.jar \
    && curl -fsSL -o /opt/syntren-patched-lib/xmlpull-1.1.3.1.jar https://repo1.maven.org/maven2/xmlpull/xmlpull/1.1.3.1/xmlpull-1.1.3.1.jar \
    && curl -fsSL -o /opt/syntren-patched-lib/xpp3_min-1.1.4c.jar https://repo1.maven.org/maven2/xpp3/xpp3_min/1.1.4c/xpp3_min-1.1.4c.jar \
    && echo "7f8039c0ee7284f9c2a9554b5e2bc20bf26b74b37f690633a75ff1993136f364  /opt/syntren-patched-lib/xstream-1.4.7.jar" | sha256sum -c - \
    && echo "34e08ee62116071cbb69c0ed70d15a7a5b208d62798c59f2120bb8929324cb63  /opt/syntren-patched-lib/xmlpull-1.1.3.1.jar" | sha256sum -c - \
    && echo "bfc90e9e32d0eab1f397fb974b5f150a815188382ac41f372a7149d5bc178008  /opt/syntren-patched-lib/xpp3_min-1.1.4c.jar" | sha256sum -c -

WORKDIR /app

COPY wrappers/simulation_data_tools/simulators/syntren/SyntrenExportSif.java /app/SyntrenExportSif.java
COPY wrappers/simulation_data_tools/simulators/syntren/run_simulator.py /app/run_simulator.py

RUN javac -cp "/opt/syntren-patched-lib/xstream-1.4.7.jar:/opt/syntren-patched-lib/xmlpull-1.1.3.1.jar:/opt/syntren-patched-lib/xpp3_min-1.1.4c.jar:/opt/syntren/SynTReN.jar:/opt/syntren/lib/cglib-nodep-2.1_3.jar:/opt/syntren/lib/colt.jar:/opt/syntren/lib/commons-math-1.1.jar:/opt/syntren/lib/xercesImpl.jar:/opt/syntren/lib/xom-1.0a5.jar" /app/SyntrenExportSif.java \
    && chmod +x /app/run_simulator.py \
    && python3 -m py_compile /app/run_simulator.py

ENTRYPOINT ["python3", "/app/run_simulator.py", "--request", "/work/request/simulator-run-request.json", "--output-dir", "/work/out"]
