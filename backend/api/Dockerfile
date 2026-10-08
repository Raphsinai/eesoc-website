FROM python:3.14

WORKDIR /code

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# main.py sits at the service root, and that root goes on sys.path — which is
# why there is deliberately no __init__.py here and imports are absolute.
COPY . /code

# --timeout-graceful-shutdown: on SIGTERM (a RollingUpdate stops this container
# with one) uvicorn stops accepting connections and finishes what is in flight
# before exiting. Together with the preStop sleep in deploy/k8s/base/api.yaml —
# which holds SIGTERM back until the endpoint has left Service load balancing —
# this is what lets a pod be replaced without dropping the request it is serving.
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "15"]
