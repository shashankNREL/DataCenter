FROM ubuntu:22.04@sha256:cb2af41f42b9c9bc9bcdc7cf1735e3c4b3d95b2137be86fd940373471a34c8b0

RUN apt update && \
    apt install -y python3 python3-pip git

ENV RAPS_DIR=/home/raps
WORKDIR ${RAPS_DIR}

RUN pip install --upgrade pip
RUN pip install hatch

COPY pyproject.toml ${RAPS_DIR}
RUN hatch dep show requirements > ${RAPS_DIR}/requirements.txt
RUN pip install -r requirements.txt
COPY . ${RAPS_DIR}

CMD ["python3", "main.py", "-c"]
