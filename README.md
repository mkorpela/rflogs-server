# RFLogs Server

Open source API server for managing and analyzing Robot Framework test results. The server provides a foundation for building test result management systems with features for storing, retrieving, and organizing test artifacts.

## Features

- Store and manage Robot Framework test results
- Upload and retrieve test artifacts (logs, reports, screenshots)
- Organize test runs by projects
- Extensible storage backend interface
- OpenID Connect based authentication
- RESTful API with OpenAPI documentation

## Quick Start

1. Clone the repository:
```bash
git clone https://github.com/mkorpela/rflogs-server.git
cd rflogs-server
```

2. Install poetry if you haven't already:
```bash
pip install poetry
```

3. Install dependencies:
```bash
poetry install
```

4. Run the server:
```bash
poetry run uvicorn rflogs_server.main:app --reload
```

## Development

### Running Tests
```bash
poetry run pytest
```

### Code Style
The project uses:
- black for code formatting
- isort for import sorting
- mypy for type checking
- ruff for linting

To run all checks:
```bash
poetry run black .
poetry run isort .
poetry run mypy rflogs_server
poetry run ruff check .
```

### Docker Development

Build and run using Docker:
```bash
docker build -t rflogs-server .
docker run -p 8000:8000 rflogs-server
```

## Environment Variables

- `STORAGE_BACKEND`: Storage backend to use ('s3' or 'local')
- `AWS_ACCESS_KEY_ID`: AWS access key (when using S3 backend)
- `AWS_SECRET_ACCESS_KEY`: AWS secret key (when using S3 backend)
- `AWS_DEFAULT_REGION`: AWS region (when using S3 backend)
- `DATABASE_URL`: PostgreSQL database URL
- `OIDC_ISSUER`: OpenID Connect issuer URL
- `OIDC_CLIENT_ID`: OpenID Connect client ID
- `OIDC_CLIENT_SECRET`: OpenID Connect client secret

## Documentation

See the [docs](docs/) directory for detailed documentation:
- [API Reference](docs/api.md)
- [Storage Backends](docs/storage.md)
- [Authentication](docs/auth.md)
- [Database Schema](docs/database.md)

## Contributing

We welcome contributions! Please feel free to submit a Pull Request.

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

The AGPL-3.0 license ensures that if you make modifications to this code and run it as a service, you must make your modifications available to users of that service under the same license.