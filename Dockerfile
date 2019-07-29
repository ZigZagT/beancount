FROM python:3
COPY . /tmp/beancount
RUN apt-get install -y \
    && pip install /tmp/beancount \
    && bean-check /dev/null
WORKDIR /data
