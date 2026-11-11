FROM node:22.23.4-bookworm-slim
COPY . /tmp/benchpress-payload
RUN sh /tmp/benchpress-payload/prepare.sh /opt/benchpress && rm -rf /tmp/benchpress-payload
