FROM python:3
ARG BRANCH=master
RUN apt-get install -y \
    && pip install git+https://github.com/BananaWanted/beancount.git@$BRANCH \
    && bean-check /dev/null
WORKDIR /data
