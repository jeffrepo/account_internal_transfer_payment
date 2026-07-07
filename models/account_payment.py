from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from markupsafe import Markup



class AccountPayment(models.Model):
    _inherit = "account.payment"

    is_internal_transfer = fields.Boolean(
        string="Transferencia interna",
        copy=False,
    )
    destination_journal_id = fields.Many2one(
        "account.journal",
        string="Diario de destino",
        check_company=True,
        copy=False,
        domain="[('type', 'in', ('bank', 'cash')), ('company_id', '=', company_id), ('id', '!=', journal_id)]",
    )
    paired_internal_transfer_payment_id = fields.Many2one(
        "account.payment",
        string="Pago espejo",
        copy=False,
        readonly=True,
        check_company=True,
    )
    internal_transfer_pair_created = fields.Boolean(
        string="Par creado",
        default=False,
        copy=False,
        readonly=True,
    )

    @api.onchange("is_internal_transfer")
    def _onchange_is_internal_transfer(self):
        for rec in self:
            if rec.is_internal_transfer:
                internal_partner = rec._get_internal_transfer_partner()
                rec.partner_id = internal_partner
                rec.partner_type = "supplier"
                rec.payment_type = "outbound"
                if "partner_bank_id" in rec._fields:
                    rec.partner_bank_id = False
            else:
                rec.destination_journal_id = False

    @api.onchange("journal_id", "company_id", "is_internal_transfer")
    def _onchange_destination_journal_id_domain(self):
        if not self.is_internal_transfer:
            return
        return {
            "domain": {
                "destination_journal_id": [
                    ("type", "in", ("bank", "cash")),
                    ("company_id", "=", self.company_id.id),
                    ("id", "!=", self.journal_id.id),
                ]
            }
        }

    def _get_internal_transfer_partner(self):
        self.ensure_one()
        if not self.company_id.partner_id:
            raise ValidationError(_("La compañía no tiene un contacto configurado."))
        return self.company_id.partner_id
        
    @api.constrains("is_internal_transfer", "journal_id", "destination_journal_id", "company_id")
    def _check_internal_transfer_configuration(self):
        for rec in self.filtered("is_internal_transfer"):
            if not rec.journal_id:
                raise ValidationError(_("Debe seleccionar un diario origen."))
            if not rec.destination_journal_id:
                raise ValidationError(_("Debe seleccionar un diario destino."))
            if rec.journal_id == rec.destination_journal_id:
                raise ValidationError(_("El diario origen y destino deben ser diferentes."))
            if rec.journal_id.company_id != rec.destination_journal_id.company_id:
                raise ValidationError(_("Los diarios deben pertenecer a la misma compañía."))
            if rec.journal_id.type not in ("bank", "cash") or rec.destination_journal_id.type not in ("bank", "cash"):
                raise ValidationError(_("Solo se permiten diarios de banco o caja."))
            if not rec.company_id.transfer_account_id:
                raise ValidationError(_("Configura la cuenta de transferencia interna en la compañía."))

    @api.depends("journal_id", "partner_id", "partner_type", "is_internal_transfer", "company_id")
    def _compute_destination_account_id(self):
        super()._compute_destination_account_id()
        for pay in self:
            if pay.is_internal_transfer:
                pay.destination_account_id = pay.company_id.transfer_account_id

    def _prepare_move_liquidity_lines(self, default_values):
        lines = super()._prepare_move_liquidity_lines(default_values)
        if self.is_internal_transfer:
            for line in lines:
                line["partner_id"] = False
        return lines

    def _prepare_move_counterpart_lines(self, default_values):
        lines = super()._prepare_move_counterpart_lines(default_values)
        if self.is_internal_transfer:
            for line in lines:
                line["partner_id"] = False
                line["account_id"] = self.company_id.transfer_account_id.id
        return lines

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("is_internal_transfer"):
                company = self.env["res.company"].browse(vals.get("company_id")) if vals.get("company_id") else self.env.company
                if not company.partner_id:
                    raise ValidationError(_("La compañía no tiene un contacto configurado."))
                vals["partner_id"] = company.partner_id.id
                vals["partner_type"] = vals.get("partner_type") or "supplier"
                vals["payment_type"] = "outbound"
        return super().create(vals_list)
    
    def write(self, vals):
        vals = dict(vals)
        for rec in self:
            if vals.get("is_internal_transfer") or rec.is_internal_transfer:
                company = rec.company_id or self.env.company
                if not vals.get("company_id") and company.partner_id:
                    vals.setdefault("partner_id", company.partner_id.id)
                vals.setdefault("partner_type", "supplier")
                vals.setdefault("payment_type", "outbound")
        return super().write(vals)

    def _prepare_internal_transfer_pair_vals(self):
        self.ensure_one()
    
        inbound_method_line = self.destination_journal_id.inbound_payment_method_line_ids[:1]
        if not inbound_method_line:
            raise UserError(
                _("El diario destino %s no tiene método de pago de entrada configurado.")
                % self.destination_journal_id.display_name
            )
    
        internal_partner = self.company_id.partner_id
        if not internal_partner:
            raise ValidationError(_("La compañía no tiene un contacto configurado."))
    
        destination_currency = self.destination_journal_id.currency_id or self.company_id.currency_id
    
        if destination_currency == self.currency_id:
            destination_amount = self.amount
        else:
            destination_amount = self.currency_id._convert(
                self.amount,
                destination_currency,
                self.company_id,
                self.date,
            )
    
        return {
            "date": self.date,
            "journal_id": self.destination_journal_id.id,
            "company_id": self.company_id.id,
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": internal_partner.id,
            "amount": destination_amount,
            "currency_id": destination_currency.id,
            "memo": self.memo or _("Transferencia interna desde %s") % self.journal_id.display_name,
            "payment_method_line_id": inbound_method_line.id,
            "is_internal_transfer": False,
            "destination_account_id": self.company_id.transfer_account_id.id,
            "paired_internal_transfer_payment_id": self.id,
        }

    def _post_internal_transfer_links_to_chatter(self, other_payment):
        self.ensure_one()
    
        payment_link = Markup(
            '<a href="/web#id=%s&model=account.payment&view_type=form">%s</a>'
        ) % (other_payment.id, other_payment.display_name)
    
        body = Markup("Se ha creado un segundo pago: %s") % payment_link
    
        if other_payment.move_id:
            move_link = Markup(
                '<a href="/web#id=%s&model=account.move&view_type=form">%s</a>'
            ) % (other_payment.move_id.id, other_payment.move_id.display_name)
    
            body += Markup("<br/>Se ha generado el asiento: %s") % move_link
    
        self.message_post(
            body=body,
            subtype_xmlid="mail.mt_note",
        )
        
    def _create_internal_transfer_pair(self):
        for pay in self.filtered(lambda p: p.is_internal_transfer and not p.paired_internal_transfer_payment_id):
            pair_vals = pay._prepare_internal_transfer_pair_vals()
            pair = self.env["account.payment"].create(pair_vals)
    
            pay.paired_internal_transfer_payment_id = pair.id
            pay.internal_transfer_pair_created = True
    
            pair.action_post()
    
            counterpart_origin = pay.move_id.line_ids.filtered(
                lambda l: l.account_id == pay.company_id.transfer_account_id and not l.reconciled
            )
            counterpart_pair = pair.move_id.line_ids.filtered(
                lambda l: l.account_id == pay.company_id.transfer_account_id and not l.reconciled
            )
    
            lines_to_reconcile = counterpart_origin | counterpart_pair
            if len(lines_to_reconcile) >= 2:
                lines_to_reconcile.reconcile()
    
            pay._post_internal_transfer_links_to_chatter(pair)
            pair._post_internal_transfer_links_to_chatter(pay)

    def action_post(self):
        res = super().action_post()
        self.filtered(lambda p: p.is_internal_transfer and not p.internal_transfer_pair_created)._create_internal_transfer_pair()
        return res
