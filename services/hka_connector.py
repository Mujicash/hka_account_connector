import logging
from datetime import datetime
import requests


_logger = logging.getLogger(__name__)


class HKAConnector:
    """
    Connector client for The Factory HKA (OSE/PSE) web service API.

    This class handles authentication and communication with HKA's REST API,
    based on company-specific configuration.

    The connector uses the company’s RUC, credentials, and mode (test or production)
    to interact with the remote endpoints.

    Attributes:
        TEST_URL (str): URL for test environment.
        PROD_URL (str): URL for production environment.
    """

    TEST_URL = "http://demoint.thefactoryhka.com.pe/clients/ServiceClients.svc"
    PROD_URL = "http://prod.thefactoryhka.com.pe/clients/ServiceClients.svc"

    def __init__(self, env):
        """
        Initialize the HKA connector using the current user's company configuration.

        :param env: Odoo environment (self.env)
        """
        self.env = env
        company = env.user.company_id
        self.ruc = company.vat
        self.user = company.hka_user
        self.password = company.hka_password

        if company.hka_test_mode:
            self.base_url = self.TEST_URL
            self.app_type = "I"
        else:
            self.base_url = self.PROD_URL
            self.app_type = "P"
        
        _logger.info(
            "HKA Connector initialized with base_url: %s, app_type: %s, user: %s, password: %s, ruc: %s", 
            self.base_url, self.app_type, self.user, self.password, self.ruc
        )

        config = env['hka.connector.config'].sudo().get_singleton()
        _logger.info("config HKA: %s", config)

        self.token = config.token
        self.token_expiry = config.token_expiry

    def authenticate(self):
        """
        Authenticate against the HKA API and store the token and its expiration date.

        :raises HTTPError: If the authentication request fails.
        :raises ValueError: If the response indicates an authentication error.
        """
        url = f"{self.base_url}/Autenticacion"
        payload = {
            "usuario": self.user,
            "clave": self.password,
            "ruc": self.ruc,
            "tipoAplicacion": self.app_type,
        }

        _logger.info("Authenticating to HKA with user: %s", self.user)
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        _logger.info("response HKA: %s", data)
        
        # if data.get('codigo') != '0':
        #     raise ValueError(f"HKA Auth error: {data.get('mensaje')}")
        
        expiry = datetime.strptime(data['fechaExpiracion'], "%Y-%m-%d %H:%M:%S")
        config = self.env['hka.connector.config'].sudo().get_singleton()
        config.write({
            'token': data['token'],
            'token_expiry': expiry,
        })
        
        self.token = data['token']
        self.token_expiry = expiry

        _logger.info("HKA authentication successful. Token valid until: %s", expiry)

    def _ensure_token(self):
        """
        Ensure that the token is valid. If not, authenticate again.
        """
        if not self.token or not self.token_expiry or datetime.now() >= self.token_expiry:
            _logger.info("HKA token missing or expired. Re-authenticating.")
            self.authenticate()

    def send_document(self, payload):
        """
        Send an electronic document to the HKA API.

        :param payload: Dictionary with the electronic document to send.
        :type payload: dict
        :return: Response from the HKA API.
        :rtype: dict
        :raises HTTPError: If the request fails.
        """
        self._ensure_token()
        url = f"{self.base_url}/Enviar"
        body = {
            "documentoElectronico": payload,
            "ruc": self.ruc,
            "token": self.token,
        }

        _logger.info("Sending electronic document to HKA: %s", body)
        resp = requests.post(url, json=body, timeout=60)
        resp.raise_for_status()
        _logger.info("HKA response: %s", resp.text)

        return resp.json()
    
    def download_file(self, document_number, file_type):
        """
        Download a file (XML, CDR, PDF) for a given document number.

        :param document_number: Document identifier, e.g., '01-F002-1'.
        :type document_number: str
        :param file_type: Type of file to download ('XML', 'CDR', or 'PDF').
        :type file_type: str
        :return: Response from the HKA API.
        :rtype: dict
        :raises HTTPError: If the request fails.
        """
        self._ensure_token()
        full_document = f"{self.ruc}-{document_number}"
        url = f"{self.base_url}/DescargaArchivo"
        payload = {
            "ruc":         self.ruc,
            "token":       self.token,
            "documento":   full_document,
            "tipoArchivo": file_type,
        }

        _logger.info("Downloading %s for document %s", file_type, full_document)
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        
        _logger.info("Download response for %s: %s", file_type, data)
        return data
