from odoo import models, api
from ..services.hka_connector import HKAConnector

class HKAConnectorService(models.AbstractModel):
    """
    Abstract service model to provide access to the HKAConnector client.

    This class serves as a factory for creating an instance of HKAConnector,
    using the current Odoo environment. It is designed to be reused by other
    services or models that require interaction with the HKA API.
    """

    _name = 'hka.connector.service'
    _description = 'Factory service for HKAConnector'

    @api.model
    def get_client(self):
        """
        Instantiate and return an HKAConnector client.

        :return: HKAConnector instance with the current environment.
        :rtype: HKAConnector
        """
        return HKAConnector(self.env)