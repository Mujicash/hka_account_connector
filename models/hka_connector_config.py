from odoo import models, fields, api

class HKAConnectorConfig(models.TransientModel):
    """
    Transient model used to temporarily store the authentication token and expiration
    for the HKA API session.

    This model acts as a lightweight configuration storage used by the connector
    to persist the current session token and its validity period.
    """

    _name = 'hka.connector.config'
    _description = 'Temporary Configuration for HKA Connector'

    token = fields.Char(string="Token HKA")
    token_expiry = fields.Datetime(string="Token Expiration Date")

    @api.model
    def get_singleton(self):
        """
        Retrieve the singleton configuration record.
        If no record exists, a new one is created.

        :return: Singleton instance of the configuration.
        :rtype: hka.connector.config
        """
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return config