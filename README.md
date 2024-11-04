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

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install development dependencies:
```bash
pip install -e ".[dev]"
```

4. Run the server:
```bash
python -m rflogs.main
```

## Development

### Running Tests
```bash
pytest
```

### Code Style
The project uses:
- black for code formatting
- isort for import sorting
- mypy for type checking
- ruff for linting

To run all checks:
```bash
black .
isort .
mypy src
ruff check .
```

## Documentation

See the [docs](docs/) directory for detailed documentation.

## Contributing

We welcome contributions! Please feel free to submit a Pull Request.

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

The AGPL-3.0 license ensures that if you make modifications to this code and run it as a service, you must make your modifications available to users of that service under the same license.