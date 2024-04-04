#!/bin/bash

set -eo pipefail

cd "$(dirname $0)"

set -x
git pull
docker-compose up --build -d
