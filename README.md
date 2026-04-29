# account_internal_transfer_payment

Primer borrador funcional para Odoo 19.

## Objetivo
Recuperar una experiencia parecida a Odoo 15 en la pantalla de pagos:
- checkbox de transferencia interna
- diario origen
- diario destino
- generacion de pago espejo cuando se publica

## Alcance actual
- agrega campos a `account.payment`
- agrega vista heredada del formulario de pagos
- valida que origen y destino sean diarios tipo banco/caja de la misma compania
- intenta reutilizar el metodo nativo `_create_paired_internal_transfer_payment` si existe en el core
- si el core no genera el par, intenta una creacion fallback

## Pendientes probables de afinacion
- compatibilidad exacta con la vista final de Odoo 19 del cliente
- monedas distintas entre diario origen y destino
- cancelacion/reversion segun el flujo contable final del proyecto
- restricciones adicionales de conciliacion
- pruebas sobre staging

## Instalacion
1. Copiar el modulo a la ruta de addons personalizados.
2. Actualizar lista de aplicaciones.
3. Instalar `Account Internal Transfer Payment`.

## Nota
Este paquete es una base inicial para iterar. En ambientes reales conviene probarlo primero en staging.
