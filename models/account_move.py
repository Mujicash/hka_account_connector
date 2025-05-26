import base64
import logging
from lxml import html
from dateutil.relativedelta import relativedelta
import re

from datetime import datetime
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    """
    Extension of `account.move` to integrate electronic invoicing with The Factory HKA (OSE/PSE).

    Adds fields to track the status of the electronic invoice in HKA and methods to generate,
    send and download the necessary files (XML, PDF, CDR) for compliance.
    """

    _inherit = 'account.move'

    hka_status = fields.Selection([
        ('to_send', 'Por Enviar'),
        ('sent',    'Enviado'),
        ('accepted','Aceptado'),
        ('rejected','Rechazado'),
    ], string="Estado HKA")
    hka_cpe_number = fields.Char(string="N° CPE")
    hka_sent_date = fields.Datetime(string="Fecha Envío HKA")
    hka_error_msg = fields.Text(string="Error HKA")
    hka_retry_count = fields.Integer(string="Reintentos HKA", default=0, copy=False)
    hka_xml_file = fields.Boolean(string="XML Descargado", default=False, copy=False)
    hka_pdf_file = fields.Boolean(string="PDF Descargado", default=False, copy=False)
    hka_cdr_file = fields.Boolean(string="CDR Descargado", default=False, copy=False)

    l10n_pe_edi_operation_type_code_id = fields.Many2one(
        comodel_name="l10n_pe_edi.catalog.51",
        string="Operation Type",
        help="Operation type code for the invoice.",
    )
    l10n_pe_edi_detraction_type_id = fields.Many2one(
        comodel_name="l10n_pe_edi.catalog.54",
        string="Detraction Type",
        copy=False,
        help="SUNAT detraction type code. Required for operations subject to detraction.",
    )
    l10n_pe_edi_detraction_payment_type_id = fields.Many2one(
        comodel_name="l10n_pe_edi.catalog.59",
        string="Detraction Payment Type",
        copy=False,
        help="SUNAT payment method for detraction, e.g., deposit in Banco de la Nación.",
    )
    l10n_pe_edi_total_detraction = fields.Monetary(
        string="Total Detraction",
        store=True,
        compute="_compute_total_detraction",
        tracking=True,
    )
    l10n_pe_edi_detraction_bank_account = fields.Many2one(
        comodel_name="res.partner.bank",
        string="National bank Account",
        help="Bank account for detraction deposit, usually in Banco de la Nación."
    )
    company_partner_id = fields.Many2one(
        related='company_id.partner_id',
        string="Partner de la compañía",
        store=False,
        readonly=True
    )

    # Constants for file types
    _FILE_TYPES = {
        'XML': ('xml', 'application/xml'),
        'PDF': ('pdf', 'application/pdf'),
        'CDR': ('zip', 'application/zip'),
    }

    @api.onchange('l10n_pe_edi_detraction_type_id')
    def _onchange_detraction_type(self):
        """
        Updates detraction-related fields based on the selected detraction type.

        - If a detraction type is selected:
            - Sets the operation type to '1001' (Operation subject to detraction).
            - Sets the payment method from the selected detraction type.
            - Loads the default bank account from the company settings.
        - If the detraction type is removed:
            - Resets the operation type to '0101' (Internal sale).
            - Clears the payment method and bank account fields.
        """
        catalog51 = self.env['l10n_pe_edi.catalog.51']
        if self.l10n_pe_edi_detraction_type_id:
            # Operation type code for detraction
            self.l10n_pe_edi_operation_type_code_id = catalog51.search([('code', '=', '1001')], limit=1)
            # estos campos se habilitan en la vista
            # self.l10n_pe_edi_detraction_payment_type_id = company_id.l10n_pe_edi_detraction_payment_type_id or False
            # self.l10n_pe_edi_detraction_bank_account = company_id.l10n_pe_edi_detraction_bank_account_id or False
        else:
            # Operation type code for normal invoice
            self.l10n_pe_edi_operation_type_code_id = catalog51.search([('code', '=', '0101')], limit=1)
            self.l10n_pe_edi_detraction_payment_type_id = False
            self.l10n_pe_edi_detraction_bank_account = False

    @api.depends(
        'l10n_pe_edi_detraction_type_id',
        'l10n_pe_edi_detraction_type_id.rate',
        'amount_total'
    )
    def _compute_total_detraction(self):
        """
        Computes the total detraction amount based on the invoice total
        and the detraction percentage defined in the selected detraction type.
        """
        for move in self:
            if move.l10n_pe_edi_detraction_type_id:
                rate = move.l10n_pe_edi_detraction_type_id.rate or 0.0
                detraction = round(move.amount_total * rate / 100, 2)
                move.l10n_pe_edi_total_detraction = detraction
                # move.l10n_pe_edi_total_detraction_signed = detraction
            else:
                move.l10n_pe_edi_total_detraction = 0.0
                # move.l10n_pe_edi_total_detraction_signed = 0.0

    def _prepare_hka_header(self):
        """
        Prepare the electronic invoice header required by HKA.

        :return: Dict with header metadata.
        :rtype: dict
        """
        invoice_date = self.invoice_date.strftime("%Y-%m-%d")
        invoice_date_due = self.invoice_date_due.strftime("%Y-%m-%d")
        time_str = fields.Datetime.context_timestamp(self, datetime.now()).strftime("%H:%M:%S")
        serie, correlativo = self.name.split('-', 1)
        return {
            "fechaEmision": invoice_date,
            "fechaVencimiento": invoice_date_due,
            "horaEmision": time_str,
            "tipoDocumento": self.l10n_latam_document_type_id_code,
            # "tipoDocumento": "01",  # Factura
            "serie": serie,
            "correlativo": correlativo,
            # "serie": "F002",
            # "correlativo": "2",
            # "codigoTipoOperacion": self.l10n_pe_edi_operation_type or '0101',
            # "codigoTipoOperacion": '0101',
            "codigoTipoOperacion": self.l10n_pe_edi_operation_type_code_id.code or '0101',
        }

    def _prepare_hka_emisor(self):
        """
        Prepare issuer (company) data for the payload.

        :return: Dict with issuer info.
        :rtype: dict
        """
        company = self.company_id
        return {
            "ruc": company.vat or '',
            "nombreComercial": company.name,
            # "lugarExpedicion": company.zip or '',
            "lugarExpedicion": '0000',
            "domicilioFiscal": company.street or '',
            "urbanizacion": company.street2 or '',
            "distrito": company.city or '',
            "provincia": company.state_id.name or '',
            "departamento": company.state_id.name or '',
            "codigoPais": company.country_id.code or '',
            "ubigeo": company.partner_id.zip or '',
        }

    def _prepare_hka_receptor(self):
        """
        Prepare customer data for the payload.

        :return: Dict with customer info.
        :rtype: dict
        """
        partner = self.partner_id
        id_type = partner.l10n_latam_identification_type_id.l10n_pe_vat_code or ''
        return {
            "tipoDocumento": id_type,
            "numDocumento": partner.vat or '',
            "razonSocial": partner.name,
            "notificar": "NO",
            "pais": partner.country_id.code or '',
            "provincia": partner.state_id.name or '',
            "departamento": partner.state_id.name or '',
            "distrito": partner.city or '',
            "direccion": partner.street or '',
        }

    def _prepare_hka_items(self):
        """
        Build the list of items/services included in the invoice.

        :return: List of item dictionaries.
        :rtype: list
        """
        items = []
        for idx, line in enumerate(self.invoice_line_ids, start=1):
            # suponemos IGV único por línea
            igv = line.tax_ids.filtered(lambda t: t.amount).mapped('amount')
            igv_pct = igv[0] if igv else 0
            base = line.price_subtotal
            monto_igv = base * igv_pct / 100
            items.append({
                "numeroOrden": str(idx),
                "descripcion": line.name,
                "cantidad": str(int(line.quantity)),
                "unidadMedida": line.product_uom_id.l10n_pe_edi_uom_code_id.code,
                # "unidadMedida": 'NIU',
                "valorUnitarioBI": f"{line.price_unit:.2f}",
                "valorVentaItemQxBI": f"{base:.2f}",
                "precioVentaUnitarioItem": f"{line.price_total:.2f}",
                "montoTotalImpuestoItem": f"{monto_igv:.2f}",
                "IGV": {
                    "baseImponible": f"{base:.2f}",
                    "porcentaje": f"{igv_pct:.2f}",
                    "monto": f"{monto_igv:.2f}",
                    "tipo": "10",
                }
            })
        return items

    def _prepare_hka_totals(self):
        """
        Prepare totals section of the electronic invoice.

        :return: Dict with totals.
        :rtype: dict
        """
        return {
            "importeTotalPagar": f"{self.amount_total:.2f}",
            "importeTotalVenta": f"{self.amount_total:.2f}",
            "montoTotalImpuestos": f"{self.amount_tax:.2f}",
            "subtotalValorVenta": f"{self.amount_untaxed:.2f}",
            "totalIGV": f"{self.amount_tax:.2f}",
            "subtotal": {
                "IGV": f"{self.amount_untaxed:.2f}"
            }
        }

    def _prepare_hka_payment(self):
        """
        Define payment terms (assumes cash payment on invoice date).

        :return: Dict with payment info.
        :rtype: dict
        """
        date = self.invoice_date.strftime("%Y-%m-%d")
        return {
            "fechaInicio": date,
            "fechaFin":    date,
            "moneda":      self.currency_id.name,
            "tipoCambio": 3.75
        }
    
    def _prepare_hka_information(self):
        """
        Prepare additional information for the PDF customization.

        Extracts key-value pairs from the HTML-formatted narration field.
        Each <p> line is split by the first colon (:) into title and value.

        :return: List of dicts with 'seccion', 'titulo', and 'valor' per line.
        :rtype: list
        """
        result = []

        if self.narration:
            _logger.info(f"Preparing HKA information from narration: {self.narration}")

            doc = html.fromstring(self.narration)
            paragraphs = doc.xpath('//p')
            _logger.info(f"Paragraphs found: {paragraphs}")

            for p in paragraphs:
                full_text = p.text_content().strip()
                clean_line = re.sub(r'\s+', ' ', full_text)

                _logger.info(f"Processing line: {clean_line}")

                if ':' in clean_line:
                    key, value = map(str.strip, clean_line.split(':', 1))
                    result.append({
                        "seccion": "1",
                        "titulo": key,
                        "valor": value
                    })

        return result
    
    def _prepare_hka_detraction(self):
        """
        Prepare detraction information for the payload.

        :return: Dict with detraction info.
        :rtype: dict
        """
        return [{
            "codigo": self.l10n_pe_edi_detraction_type_id.code,
            "medioPago": self.l10n_pe_edi_detraction_payment_type_id.code,
            "monto": f"{self.l10n_pe_edi_total_detraction:.2f}",
            "numCuentaBancodelaNacion": self.l10n_pe_edi_detraction_bank_account.acc_number or '',
            "porcentaje": self.l10n_pe_edi_detraction_type_id.rate,
        }]
    
    def _prepare_hka_payment_method(self):
        """
        Define payment terms for 'facturaNegociable' in HKA.

        If the invoice uses immediate payment, sets 'Contado' mode.
        If it uses credit terms, sets 'Credito' mode and builds the list of dues.

        :return: Dict for facturaNegociable including 'modoPago', 'montoNetoPendiente', and optionally 'cuotasFactura'.
        :rtype: dict
        """
        self.ensure_one()
        immediate_term = self.env.ref('account.account_payment_term_immediate', raise_if_not_found=False)
        is_credit = self.invoice_payment_term_id and self.invoice_payment_term_id != immediate_term
        net_amount = self.amount_total - self.l10n_pe_edi_total_detraction

        payment_info = {
            "modoPago": "Credito" if is_credit else "Contado",
            "montoNetoPendiente": f"{net_amount:.2f}" if is_credit else "0"
        }

        if is_credit:
            dues = []
            lines = self.invoice_payment_term_id.line_ids.sorted(lambda l: l.days)
            total_allocated = 0.0

            for idx, line in enumerate(lines, start=1):
                if line.value == 'balance':
                    amount = net_amount - total_allocated
                elif line.value == 'percent':
                    amount = round(net_amount * line.value_amount / 100, 2)
                elif line.value == 'fixed':
                    amount = round(line.value_amount, 2)
                else:
                    amount = 0.0

                due_date = self.invoice_date + relativedelta(months=line.months, days=line.days)
                total_allocated += amount

                dues.append({
                    "fechaPagoCuota": due_date.strftime("%Y-%m-%d"),
                    "identificadorCuota": f"Cuota{idx:03}",
                    "montoPagoCuota": f"{amount:.2f}"
                })

            payment_info["cuotasFactura"] = dues

        return payment_info


    def _prepare_hka_payload(self):
        """
        Aggregate all sections into a single payload to send to HKA.

        :return: Complete JSON payload for API.
        :rtype: dict
        """
        self.ensure_one()
        _logger.info("Preparing HKA payload for %s", self.name)
        header = self._prepare_hka_header()
        emisor = self._prepare_hka_emisor()
        receptor = self._prepare_hka_receptor()
        items = self._prepare_hka_items()
        totals = self._prepare_hka_totals()
        payment = self._prepare_hka_payment()
        payment_method = self._prepare_hka_payment_method()

        payload = {
            **header,
            "emisor": emisor,
            "receptor": receptor,
            "facturaNegociable": payment_method,
            "producto": items,
            "totales": totals,
            "pago": payment,
        }

        if self.narration:
            payload["personalizacionPDF"] = self._prepare_hka_information()

        if self.l10n_pe_edi_detraction_type_id:
            payload["detraccion"] = self._prepare_hka_detraction()

        return payload

    def button_send_hka(self):
        self.ensure_one()
        if self.move_type not in ('out_invoice','out_refund'):
            raise UserError(_("Solo facturas o notas de crédito"))
        # Prepara tu payload aquí...
        payload = self._prepare_hka_payload()
        _logger.info("Payload HKA: %s", payload)

        connector = self.env['hka.connector.service'].sudo().get_client()
        _logger.info("Conectando a HKA...")

    # def get_cdr(self):
    #     try:
    #         connector = self.env['hka.connector.service'].sudo().get_client()
    #         resp = connector.download_file(self.hka_cpe_number, 'CDR')
    #         if resp.get('codigo') == 0 and resp.get('archivo'):
    #             data = base64.b64decode(resp['archivo'])
    #             self.env['ir.attachment'].create({
    #                 'name':      f"{self.name}.zip",
    #                 'type':      'binary',
    #                 'datas':     base64.b64encode(data),
    #                 'mimetype':  'application/zip',
    #                 'res_model': 'account.move',
    #                 'res_id':    self.id,
    #             })
    #             _logger.info("CDR descargado para %s", self.name)
    #         else:
    #             _logger.info("CDR no listo para %s: %s", self.name, resp.get('mensaje'))
    #     except Exception as e:
    #         _logger.error("Error al descargar CDR %s: %s", self.name, e)

    def action_post(self):
        """
        Hook into `action_post` to mark invoice as 'to_send' for HKA processing.
        """
        res = super(AccountMove, self).action_post()
        to_send = self.filtered(lambda m: m.move_type == 'out_invoice' and m.journal_id.type == 'sale')

        if to_send:
            to_send.write({
                'hka_status': 'to_send',
            })

        return res
    
    @api.model
    def _domain_pending_send(self):
        """
        Domain for invoices pending to be sent to HKA.
        """
        return [
            ('hka_status', '=', 'to_send'),
            ('move_type',   '=', 'out_invoice'),
            ('journal_id.type', '=', 'sale'),
            ('state', '=', 'posted'),
        ]

    @api.model
    def _domain_pending_download(self):
        """
        Domain for invoices whose documents need to be downloaded.
        """
        return [
            ('hka_status', '=', 'sent'),
            ('move_type',   '=', 'out_invoice'),
            ('journal_id.type', '=', 'sale'),
            ('state', '=', 'posted'),
            '|', '|',
            ('hka_xml_file', '=', False),
            ('hka_pdf_file', '=', False),
            ('hka_cdr_file', '=', False),
        ]

    # Central attach helper
    def _attach_file(self, data, ext, mimetype):
        """
        Attach a binary file (XML, PDF, or CDR) to the invoice.

        :param data: Binary content of the file.
        :param ext: File extension (e.g., 'xml').
        :param mimetype: MIME type of the file.
        """
        self.env['ir.attachment'].create({
            'name':      f"{self.name}.{ext}",
            'type':      'binary',
            'datas':     base64.b64encode(data),
            'mimetype':  mimetype,
            'res_model': 'account.move',
            'res_id':    self.id,
        })

    def _handle_retry(self, sent):
        """
        Retry handler to requeue invoices that failed to send.

        :param sent: Boolean indicating if the document was successfully sent.
        """
        max_retries = 3
        if not sent:
            self.hka_retry_count += 1
            if self.hka_retry_count >= max_retries:
                _logger.warning("Máx. reintentos alcanzados en %s", self.name)
            else:
                self.hka_status = 'to_send'
        else:
            self.hka_retry_count = 0
    
    def _send_to_hka(self, connector):
        """
        Send the invoice to HKA and update its state accordingly.

        :param connector: HKAConnector instance.
        :return: True if sent successfully, else False.
        """
        payload = self._prepare_hka_payload()
        _logger.info("Payload HKA: %s", payload)
        resp = connector.send_document(payload)

        if not resp.get('estatus'):
            self.write({
                'hka_status': 'rejected',
                'hka_error_msg': resp.get('mensaje'),
            })
            return False
        
        # success
        self.write({
            'hka_status': 'sent',
            'hka_cpe_number': resp.get('numeracion'),
            'hka_sent_date': fields.Datetime.now(),
            'hka_error_msg': False,
        })

        # attach XML
        xml_b64 = resp.get('xml') or ''
        if xml_b64:
            xml_data = base64.b64decode(xml_b64)
            ext, mimetype = self._FILE_TYPES['XML']
            self._attach_file(xml_data, ext, mimetype)
            self.hka_xml_file = True

        return True
    
    @api.model
    def _cron_send_hka(self):
        """
        Cron job to send pending invoices to HKA.
        """
        _logger.info("Ejecutando cron de envío a HKA")
        connector = self.env['hka.connector.service'].sudo().get_client()
        pending_invoices = self.search(self._domain_pending_send())

        for invoice in pending_invoices:
            try:
                sent = invoice._send_to_hka(connector)
                invoice._handle_retry(sent)

            except Exception as e:
                invoice.hka_error_msg = str(e)
                invoice.hka_retry_count += 1
                _logger.error("Error al enviar %s a HKA: %s", invoice.name, e)

    def _download_and_attach(self, connector, type, done_field):
        """
        Download a file from HKA and attach it to the invoice.

        :param connector: HKAConnector instance.
        :param type: File type ('XML', 'PDF', or 'CDR').
        :param done_field: Field name to set as True upon success.
        """
        try:
            ext, mimetype = self._FILE_TYPES[type]
            resp = connector.download_file(self.hka_cpe_number, type)

            if resp.get('codigo') == 0 and resp.get('archivo'):
                data = base64.b64decode(resp['archivo'])
                self._attach_file(data, ext, mimetype)
                setattr(self, done_field, True)
                _logger.info("%s descargado para %s", type, self.name)
            else:
                _logger.info("No se pudo descargar %s: %s", type, resp.get('mensaje'))

        except Exception as e:
            _logger.error("Error al descargar %s para %s: %s", type, self.name, e)
    
    @api.model
    def _cron_download_documents(self):
        """
        Cron job to download and attach XML, PDF and CDR files from HKA.
        """
        _logger.info("Ejecutando cron de descarga de documentos HKA")
        connector = self.env['hka.connector.service'].sudo().get_client()
        pending_invoices = self.search(self._domain_pending_download())

        for invoice in pending_invoices:
            if not invoice.hka_xml_file:
                invoice._download_and_attach(connector, 'XML', 'hka_xml_file')

            if not invoice.hka_pdf_file:
                invoice._download_and_attach(connector, 'PDF', 'hka_pdf_file')

            if not invoice.hka_cdr_file:
                invoice._download_and_attach(connector, 'CDR', 'hka_cdr_file')
    