# Authentication and Permission Notes

The authentication Lambda is `lambda_auth.py`. It signs seven-day HS256 JWTs and
manages users in `jrf-task-BreatheUsers` (partition key: `username`). User items include
`password_hash`, `role`, `email`, `permissions`, and `created_at`. Admins can grant or
remove access to the app tabs, including Atlas.

## Required configuration

Set `AWS_REGION`, `USERS_TABLE`, `JWT_SECRET`, and `ALLOWED_ORIGIN` on the auth Lambda.
Set the same `AWS_REGION`, `USERS_TABLE`, `JWT_SECRET`, and `ALLOWED_ORIGIN` on the
retrieval Lambda for its admin-only document-delete route. Keep the JWT secret in
protected Lambda configuration or a secrets manager; do not copy it into source,
documentation, logs, or command output.

The retrieval role needs:

- DynamoDB reads and deletes on `jrf-task-ocr-DocumentOCR`.
- DynamoDB query and batch delete access on `jrf-task-ocr-ExtractedRecords`.
- DynamoDB `GetItem` on the configured users table.
- S3 list and delete access for the OCR bucket's `uploads/*` and `processed/*`
  document paths.

## API Gateway

Connect these auth routes to the auth Lambda:

| Method | Route |
|---|---|
| POST | `/auth/login` |
| POST | `/auth/signup` |
| GET | `/auth/admin/users` |
| PUT | `/auth/admin/users/{username}` |

Connect `DELETE /document/{document_id}` to the retrieval Lambda. The existing
`PUT /document/{document_id}/meta` route also uses retrieval. CORS must allow
`authorization` and `content-type`; the upload bucket has separate CORS rules for
browser file transfers.

New signups receive the default app tabs in the auth Lambda. Existing users keep their
current permission list until an admin changes it in the Users view. Admin tab
permissions control the frontend navigation. The document-delete endpoint separately
checks the signed token and the current DynamoDB role. Other data routes retain their
existing server authorization behavior, so frontend tab gating must not be treated as
API security.

For table schemas and deletion behavior, read the
[backend README](./NLP-Pipeline-Backend-README.md) and
[system guide](../SYSTEM-GUIDE.md).
