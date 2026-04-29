# account_internal_transfer_payment

Primer borrador funcional para Odoo 19 que reintroduce la experiencia de transferencia interna desde la pantalla de pagos, similar a Odoo 15.

## Incluye
- Checkbox **Transferencia interna** en `account.payment`
- Campo **Diario de destino** en el formulario de pagos
- Creacion automatica del pago espejo en el diario destino
- Conciliacion automatica de las lineas de la cuenta puente de transferencias internas
- Uso de las cuentas configuradas en cada diario para los apuntes del pago, dejando la cuenta puente para conciliacion entre ambos movimientos

## Notas
- La cuenta puente se toma desde la configuracion de la compania si existe en el core.
- Las cuentas bancarias / transitorias siguen siendo las que Odoo resuelva desde la configuracion del diario y del metodo de pago.
- Este modulo es una base para ajustar segun la implementacion real del repositorio y los cambios de Odoo 19.
