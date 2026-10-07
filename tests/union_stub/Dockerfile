# The union stub. Deliberately minimal and pinned to match the backend's
# versions (backend/api/requirements.txt pins fastapi[standard]==0.141.1) so
# the stub behaves like the service it stands in for.
FROM python:3.12-slim

WORKDIR /code

# Installed inline rather than via a requirements.txt: there is nothing else
# in this image and nothing else will ever depend on the file.
RUN pip install --no-cache-dir "fastapi[standard]==0.141.1" "uvicorn==0.41.0"

# main.py imports fixtures as a top-level module, so the service root goes on
# sys.path — same layout as the real union service, no __init__.py.
COPY main.py fixtures.py /code/

# uvicorn rather than `fastapi run` so no reload/dev machinery ships in the
# image — this is a fixed fixture server, not a service under development.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
