FROM savonet/liquidsoap:v2.2.5

USER root
COPY supervisor.sh /supervisor/supervisor.sh
RUN sed -i 's/\r$//' /supervisor/supervisor.sh && chmod +x /supervisor/supervisor.sh
USER liquidsoap

WORKDIR /data

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/sh", "/supervisor/supervisor.sh"]
