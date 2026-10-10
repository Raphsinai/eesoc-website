FROM python:3.14

WORKDIR /code

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# main.py sits at the service root, and that root goes on sys.path — which is
# why there is deliberately no __init__.py here and imports are absolute.
COPY . /code

# --timeout-graceful-shutdown: on SIGTERM (a RollingUpdate stops this container
# with one) uvicorn finishes the request in flight before exiting. The api
# serves everything THROUGH this adapter, so a replacement that cut it off
# mid-SELECT would surface as a user-facing error — the preStop sleep and this
# flag together are what keep a RollingUpdate invisible.
#
# uvicorn directly, not `fastapi run`: fastapi-cli's `run` is a thin wrapper
# around uvicorn that does not accept --timeout-graceful-shutdown, so with it
# the container exited 2 on start ("No such option") and crash-looped. Compose
# overrides this CMD with `fastapi dev`, which is why no test caught it; the
# tests/container-cmd suite now checks this line's options in the built image.
# Same server and defaults as `fastapi run` (proxy headers on).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "15"]
