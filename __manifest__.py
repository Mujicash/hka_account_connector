{
    'name': 'HKA Account Connector',
    'summary': 'Electronic document integration with The Factory HKA (OSE/PSE)',
    'version': '16.0.1.0.0',
    'author': 'Andre Mujica',
    'description': "Integrates Odoo's accounting module with The Factory HKA web service to issue electronic documents.",
    'website': 'https://github.com/Mujicash/hka_account_connector',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'account',
        'l10n_latam_invoice_document',
    ],
    'data': [
        'data/cron_data.xml',
        'views/res_config_setting_views.xml',
        'views/account_move_views.xml'
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}