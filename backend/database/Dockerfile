FROM python:3.14

WORKDIR /code

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# main.py sits at the service root, and that root goes on sys.path — which is
# why there is deliberately no __init__.py here and imports are absolute.
COPY . /code

CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8080"]
