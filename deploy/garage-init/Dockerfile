# Garage's own image is distroless — it contains the `garage` binary and
# nothing else, no shell. Initialising a cluster needs conditionals and a
# retry loop, so this pairs the binary with a shell.
#
# The binary is copied from the release image rather than downloaded, so the
# init tool and the server are always the same build.
FROM dxflrs/garage:v1.1.0 AS garage

FROM alpine:3.20
COPY --from=garage /garage /usr/local/bin/garage
COPY init.sh /usr/local/bin/garage-init
RUN chmod +x /usr/local/bin/garage-init
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["garage-init"]
