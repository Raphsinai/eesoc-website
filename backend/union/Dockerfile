FROM python:3.14

WORKDIR /code

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# main.py sits at the service root, and that root goes on sys.path — which is
# why there is deliberately no __init__.py here and imports are absolute.
COPY . /code

# --timeout-graceful-shutdown: this service is Recreate (never two replicas —
# eActivities rate-limits per source IP and bans the whole society on a breach),
# so a deploy briefly removes it from the network either way — but the api's
# sync call that is mid-flight when the stop arrives still deserves an answer
# rather than a reset, and the sync records a failed run row otherwise.
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "15"]
