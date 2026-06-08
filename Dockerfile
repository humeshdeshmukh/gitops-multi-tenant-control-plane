FROM python:3.10-slim

# Install git and other system requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependencies and install
COPY app/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and templates
COPY app/ /app/app/
COPY crossplane/ /app/crossplane/
COPY argocd/ /app/argocd/
COPY policies/ /app/policies/
COPY vcluster/ /app/vcluster/

# Set environment variables
ENV REPO_PATH=/app/platform-repo.git
ENV TEMPLATES_PATH=/app/app/gitops-templates
ENV PORT=5006
EXPOSE 5006

# Start backend
CMD ["python", "app/main.py"]
