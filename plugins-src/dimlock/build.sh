#!/bin/sh
# Builds target/DimLock-<version>.jar in a throwaway Maven container (nothing installed on the host).
set -e
cd "$(dirname "$0")"
docker run --rm -u "$(id -u):$(id -g)" -e MAVEN_CONFIG=/tmp/m2 -v "$PWD":/src -v "$PWD/../.m2":/tmp/m2 -w /src \
  maven:3.9-eclipse-temurin-25 mvn -q -B -Dmaven.repo.local=/tmp/m2 package
ls -l target/*.jar
