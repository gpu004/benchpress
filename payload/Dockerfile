FROM node:26.13.1-trixie-slim
COPY . /tmp/benchpress-payload
RUN sh /tmp/benchpress-payload/prepare.sh /opt/benchpress && rm -rf /tmp/benchpress-payload
