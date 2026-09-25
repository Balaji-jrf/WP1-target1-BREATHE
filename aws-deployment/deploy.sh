#!/usr/bin/env bash
# =============================================================================
# BREATHE WP1 — Full Serverless Deployment Script
# Region: ap-south-2 (Hyderabad)
# Account: 433448338709
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
REGION="ap-south-2"
ACCOUNT_ID="433448338709"
S3_DOCS_BUCKET="jrf-task-ocr-docs-bucket"
DYNAMO_TABLE="DocumentOCR"
ECR_REPO="breathe-ocr-backend"
PROCESSOR_FN="breathe-ocr-processor"
RETRIEVAL_FN="breathe-ocr-retrieval"
LAMBDA_ROLE="breathe-ocr-lambda-role"
API_NAME="breathe-ocr-api"
IMAGE_TAG="latest"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   BREATHE OCR — AWS Serverless Deployment            ║"
echo "║   Region: ${REGION}                          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── STEP 1: S3 Bucket ─────────────────────────────────────────────────────────
echo "▶ [1/7] Creating S3 bucket: ${S3_DOCS_BUCKET}"
aws s3api create-bucket \
    --bucket "${S3_DOCS_BUCKET}" \
    --region "${REGION}" \
    --create-bucket-configuration LocationConstraint="${REGION}" 2>/dev/null || \
    echo "   ℹ Bucket already exists, continuing..."

aws s3api put-bucket-versioning \
    --bucket "${S3_DOCS_BUCKET}" \
    --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
    --bucket "${S3_DOCS_BUCKET}" \
    --public-access-block-configuration \
        BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "   ✓ S3 bucket ready: s3://${S3_DOCS_BUCKET}"

# ── STEP 2: DynamoDB Table ────────────────────────────────────────────────────
echo ""
echo "▶ [2/7] Creating DynamoDB table: ${DYNAMO_TABLE}"
aws dynamodb create-table \
    --table-name "${DYNAMO_TABLE}" \
    --attribute-definitions AttributeName=document_id,AttributeType=S \
    --key-schema AttributeName=document_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "${REGION}" 2>/dev/null || \
    echo "   ℹ Table already exists, continuing..."

aws dynamodb wait table-exists --table-name "${DYNAMO_TABLE}" --region "${REGION}"
echo "   ✓ DynamoDB table ready: ${DYNAMO_TABLE}"

# ── STEP 3: IAM Role for Lambda ───────────────────────────────────────────────
echo ""
echo "▶ [3/7] Setting up IAM role: ${LAMBDA_ROLE}"

TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "lambda.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}'

aws iam create-role \
    --role-name "${LAMBDA_ROLE}" \
    --assume-role-policy-document "${TRUST_POLICY}" 2>/dev/null || \
    echo "   ℹ Role already exists, continuing..."

# Attach managed policies
aws iam attach-role-policy \
    --role-name "${LAMBDA_ROLE}" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Inline policy for S3 + DynamoDB access
INLINE_POLICY="{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"s3:PutObject\", \"s3:GetObject\", \"s3:DeleteObject\"],
      \"Resource\": \"arn:aws:s3:::${S3_DOCS_BUCKET}/*\"
    },
    {
      \"Effect\": \"Allow\",
      \"Action\": [\"dynamodb:PutItem\", \"dynamodb:GetItem\", \"dynamodb:UpdateItem\", \"dynamodb:Query\"],
      \"Resource\": \"arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${DYNAMO_TABLE}\"
    }
  ]
}"

aws iam put-role-policy \
    --role-name "${LAMBDA_ROLE}" \
    --policy-name "breathe-ocr-s3-dynamo-policy" \
    --policy-document "${INLINE_POLICY}"

ROLE_ARN=$(aws iam get-role --role-name "${LAMBDA_ROLE}" --query "Role.Arn" --output text)
echo "   ✓ IAM role ready: ${ROLE_ARN}"
echo "   ⏳ Waiting 15s for IAM propagation..."
sleep 15

# ── STEP 4: ECR Repository & Docker Push ─────────────────────────────────────
echo ""
echo "▶ [4/7] Building and pushing Docker image to ECR"

aws ecr create-repository \
    --repository-name "${ECR_REPO}" \
    --region "${REGION}" 2>/dev/null || \
    echo "   ℹ ECR repo already exists, continuing..."

echo "   Logging into ECR..."
aws ecr get-login-password --region "${REGION}" | \
    docker login --username AWS --password-stdin \
    "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "   Building Docker image (this takes ~5-10 min for Surya OCR)..."
docker build -t "${ECR_REPO}:${IMAGE_TAG}" "$(dirname "$0")"

docker tag "${ECR_REPO}:${IMAGE_TAG}" "${ECR_URI}"
docker push "${ECR_URI}"
echo "   ✓ Image pushed: ${ECR_URI}"

# ── STEP 5: Lambda Functions ──────────────────────────────────────────────────
echo ""
echo "▶ [5/7] Deploying Lambda functions"

# Common env vars
ENV_VARS="Variables={S3_BUCKET=${S3_DOCS_BUCKET},DYNAMO_TABLE=${DYNAMO_TABLE},MAX_UPLOAD_MB=50}"

# Processor Lambda
echo "   Creating/updating Processor Lambda..."
aws lambda create-function \
    --function-name "${PROCESSOR_FN}" \
    --package-type Image \
    --code ImageUri="${ECR_URI}" \
    --role "${ROLE_ARN}" \
    --environment "${ENV_VARS}" \
    --timeout 300 \
    --memory-size 3008 \
    --region "${REGION}" \
    --image-config Command=["lambda_processor.handler"] 2>/dev/null || \
aws lambda update-function-code \
    --function-name "${PROCESSOR_FN}" \
    --image-uri "${ECR_URI}" \
    --region "${REGION}"

aws lambda wait function-active --function-name "${PROCESSOR_FN}" --region "${REGION}"
echo "   ✓ Processor Lambda deployed"

# Retrieval Lambda
echo "   Creating/updating Retrieval Lambda..."
aws lambda create-function \
    --function-name "${RETRIEVAL_FN}" \
    --package-type Image \
    --code ImageUri="${ECR_URI}" \
    --role "${ROLE_ARN}" \
    --environment "${ENV_VARS}" \
    --timeout 30 \
    --memory-size 512 \
    --region "${REGION}" \
    --image-config Command=["lambda_retrieval.handler"] 2>/dev/null || \
aws lambda update-function-code \
    --function-name "${RETRIEVAL_FN}" \
    --image-uri "${ECR_URI}" \
    --region "${REGION}"

aws lambda wait function-active --function-name "${RETRIEVAL_FN}" --region "${REGION}"
echo "   ✓ Retrieval Lambda deployed"

# ── STEP 6: API Gateway ───────────────────────────────────────────────────────
echo ""
echo "▶ [6/7] Setting up API Gateway (HTTP API)"

API_ID=$(aws apigatewayv2 create-api \
    --name "${API_NAME}" \
    --protocol-type HTTP \
    --cors-configuration \
        AllowOrigins="*",AllowMethods="GET,POST,OPTIONS",AllowHeaders="Content-Type" \
    --region "${REGION}" \
    --query "ApiId" --output text 2>/dev/null || \
    aws apigatewayv2 get-apis --region "${REGION}" \
        --query "Items[?Name=='${API_NAME}'].ApiId" --output text)

echo "   API ID: ${API_ID}"

PROCESSOR_ARN=$(aws lambda get-function \
    --function-name "${PROCESSOR_FN}" --region "${REGION}" \
    --query "Configuration.FunctionArn" --output text)

RETRIEVAL_ARN=$(aws lambda get-function \
    --function-name "${RETRIEVAL_FN}" --region "${REGION}" \
    --query "Configuration.FunctionArn" --output text)

# Integration for Processor
PROC_INTEGRATION_ID=$(aws apigatewayv2 create-integration \
    --api-id "${API_ID}" \
    --integration-type AWS_PROXY \
    --integration-uri "${PROCESSOR_ARN}" \
    --payload-format-version "2.0" \
    --region "${REGION}" \
    --query "IntegrationId" --output text)

# Integration for Retrieval
RETR_INTEGRATION_ID=$(aws apigatewayv2 create-integration \
    --api-id "${API_ID}" \
    --integration-type AWS_PROXY \
    --integration-uri "${RETRIEVAL_ARN}" \
    --payload-format-version "2.0" \
    --region "${REGION}" \
    --query "IntegrationId" --output text)

# Routes
aws apigatewayv2 create-route \
    --api-id "${API_ID}" \
    --route-key "POST /process-ocr" \
    --target "integrations/${PROC_INTEGRATION_ID}" \
    --region "${REGION}" > /dev/null

aws apigatewayv2 create-route \
    --api-id "${API_ID}" \
    --route-key "GET /document/{document_id}" \
    --target "integrations/${RETR_INTEGRATION_ID}" \
    --region "${REGION}" > /dev/null

# Auto-deploy stage
aws apigatewayv2 create-stage \
    --api-id "${API_ID}" \
    --stage-name "prod" \
    --auto-deploy \
    --region "${REGION}" > /dev/null 2>/dev/null || true

# Lambda permissions for API Gateway
aws lambda add-permission \
    --function-name "${PROCESSOR_FN}" \
    --statement-id "apigw-processor-invoke" \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/*/process-ocr" \
    --region "${REGION}" 2>/dev/null || true

aws lambda add-permission \
    --function-name "${RETRIEVAL_FN}" \
    --statement-id "apigw-retrieval-invoke" \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/*/document/*" \
    --region "${REGION}" 2>/dev/null || true

API_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com/prod"
echo "   ✓ API Gateway ready"
echo "   ✓ POST ${API_URL}/process-ocr"
echo "   ✓ GET  ${API_URL}/document/{document_id}"

# ── STEP 7: Summary ───────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ✅ DEPLOYMENT COMPLETE                             ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  S3 Bucket    : s3://${S3_DOCS_BUCKET}"
echo "  DynamoDB     : ${DYNAMO_TABLE}"
echo "  ECR Image    : ${ECR_URI}"
echo "  Processor Fn : ${PROCESSOR_FN}"
echo "  Retrieval Fn : ${RETRIEVAL_FN}"
echo "  API Base URL : ${API_URL}"
echo ""
echo "  Set this in your React frontend:"
echo "  REACT_APP_API_URL=${API_URL}"
echo ""
echo "  Quick test:"
echo "  curl -X POST ${API_URL}/process-ocr -F 'file=@your_doc.pdf'"
echo "  curl ${API_URL}/document/<document_id>"
