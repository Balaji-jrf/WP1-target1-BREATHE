FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y wget tar gzip libgomp && yum clean all

RUN pip install --upgrade pip

COPY requirements-lambda.txt .

# Step 1: Install torch CPU wheel from PyTorch index (has correct manylinux wheel)
RUN pip install "torch==2.6.0+cpu" --index-url https://download.pytorch.org/whl/cpu \
    --target "${LAMBDA_TASK_ROOT}"

# Step 2: Install remaining deps with pre-built wheels only
RUN pip install \
    "boto3>=1.34,<2" \
    "Pillow>=10,<12" \
    "PyMuPDF==1.24.14" \
    "python-multipart>=0.0.9,<1" \
    --only-binary=PyMuPDF,Pillow \
    --target "${LAMBDA_TASK_ROOT}"

# Step 3: Install surya-ocr pinned to 0.22.0 (compatible with torch 2.6) without re-pulling torch
RUN pip install surya-ocr==0.22.0 \
    --no-deps \
    --target "${LAMBDA_TASK_ROOT}" && \
    pip install transformers accelerate numpy opencv-python-headless \
    --only-binary=:all: \
    --target "${LAMBDA_TASK_ROOT}"

# Copy Lambda handler files
COPY lambda_processor.py ${LAMBDA_TASK_ROOT}/
COPY lambda_retrieval.py ${LAMBDA_TASK_ROOT}/
COPY backend_core.py ${LAMBDA_TASK_ROOT}/

# Default handler (overridden per function via Lambda config)
CMD ["lambda_processor.handler"]
