# Ecosistema de Herramientas Externas para el Bot

Este documento recopila las herramientas externas recomendadas que pueden integrarse al "Funding Arb Bot" de forma gratuita para elevarlo a estándares de calidad institucional. Están categorizadas por su función operativa.

---

## 1. Archivo y Control de Versiones (Versionamiento)

### **Git + GitHub Privado / GitLab**
- **¿Qué resuelve?** El bot mantiene configuraciones, bases de datos y scripts sensibles. Hacer copias de carpetas (ej. `data/legacy`) induce a confusión y riesgo de perder código valioso o romper la aplicación sin marcha atrás.
- **Ventaja para el bot:** Te permite ramificar (branches). Si querés agregar la funcionalidad *Maker/Taker* de la v0.6, puedes crear un _branch_, romper todo lo que quieras, y la versión que está operando tu dinero (Main) sigue segura.
- **Implementación futura:**
  ```bash
  # Iniciarás el entorno
  git init
  # Tendremos un .gitignore para excluir claves privadas (.env) y la DB pesada (arb_bot.db)
  git add .
  git commit -m "Arquitectura estabilizada v0.5"
  ```

---

## 2. Correr el Bot (Hosting & Resiliencia)

### **Docker / Docker Compose**
- **¿Qué resuelve?** Hoy dependes de tu Virtual Environment (`.venv`) de Python en esa máquina específica. Si cambiás de OS, actualizas un paquete o migrás de servidor, el bot puede rechazar ejecutarse.
- **Ventaja para el bot:** Convierte todo el sistema en un solo "Bloque". Escribís `docker-compose up -d` y va a instalar Python, descargar los requirements, inicializar el servidor Público y Privado exactamente igual sea en tu Linux o en la nube.
- **Costo:** 100% Gratis y Open Source.

### **PM2 o Systemd**
- **¿Qué resuelve?** El bot actualmente se ejecuta con `nohup` o en consolas abiertas. Si ocurre un fallo eléctrico leve de internet o el proceso colapsa por un Memory Leak, el bot se "cae", dejándote desprotegido financieramente.
- **Ventaja para el bot:** Son demonios manejadores de procesos. Si integrás `pm2 start funding_arb_server.py`, el sistema "vigila" al proceso 24/7. Si explota, PM2 lo resucita automáticamente en `0.2 segundos` y archiva un log de por qué colapsó.
- **Costo:** 100% Gratis.

### **Oracle Cloud Free Tier (Servidor VPS Nube)**
- **¿Qué resuelve?** No siempre podés o querés depender de la estabilidad eléctrica/Internet de tu propia casa cuando hay miles de dólares en riesgo automatizado.
- **Ventaja para el bot:** Oracle otorga una instancia Linux ARM gratuitamente de por vida. Vos subís tu bot ahí, le metés el PM2 y te olvidás. Podés abrir la UI web desde el navegador de tu celular en la calle.

---

## 3. Debuggeo, Análisis de Rendimiento y Alertas

### **Sentry**
- **¿Qué resuelve?** Mirar el archivo `bot_app.log` o la consola en busca del "motivo del fallo" es primitivo y no es proactivo.
- **Ventaja para el bot:** Sentry atrapa excepciones antes de que tiren la App. Inyecta dos líneas de código y, si falla una petición Lighter o el JSON de Hyperliquid viene corrupto, salta una alerta automáticamente a tu correo y te muestra qué variables exactas generaron el crash.  
- **Costo:** Free Tier extremadamente amplio, ideal para proyectos individuales.

### **UptimeRobot**
- **¿Qué resuelve?** Querés saber de inmediato si tu bot se aisló de internet sin tener que refrescar manualmene el dashboard de tu casa.
- **Ventaja para el bot:** Registramos el Endpoint interno `/api/doctor` a una URL o webhook. Uptime robot revisa tu sistema cada 5 mins desde servidores internacionales. Si deja de contestar, avisa al instante.
- **Costo:** Free Tier (Monitoreo cada 5 mins).

### **Prometheus + Grafana**
- **¿Qué resuelve?** Si el bot opera capital por meses, necesitas analíticas que tracen en gráficos la salud de tu cuenta y el comportamiento del Spread a través del tiempo, no solo en "el momento actual" o una lista estática.
- **Ventaja para el bot:** Grafana crearía paneles visuales increíbles. Podrías ver un gráfico de agujas con el PnL absoluto, el PnL simulado, y gráficos de barras de tu capital en cada exchange.
- **Costo:** 100% Gratis instalándolo en local.

---

## 4. Analizador Estático (Calidad de Código)

### **Ruff / MyPy**
- **¿Qué resuelve?** En Python, un mal tabulado, un archivo sin importar o mandar un string (`"BTC"`) en lugar de un diccionario a una función crítica a veces solo explota cuando sucede *en ejecución* (por ejemplo, enviando una orden a mercado).
- **Ventaja para el bot:** Con Ruff y MyPy estricto, analizamos tu código ANTES de ejecutar. Impedirá que una orden a mercado falle por errores estúpidos perdiendo dinero por caídas tontas.
- **Costo:** Gratis.

---
**Notas para el Desarrollador:** Si decidís arrancar mañana, te recomiendo empezar blindando el código con **Git**, para luego aplicar estabilizadores operacionales rápidos como **PM2**, antes de avanzar hacia monitoreos complejos como Sentry o Grafana.
