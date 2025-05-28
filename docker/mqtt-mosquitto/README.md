# MQTT Mosquitto
This is a Docker application that runs an MQTT Mosquitto broker. 

# Requirements
1. Python 3.9 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Docker (optional) for upload the application to a Kelvin Instance.

# Kelvin Cloud Deployment
To deploy this application to a cluster using the Kelvin Cloud you need to setup the environment variables as Secrets.

```
kelvin secret create mqtt-user --value "<user>"
kelvin secret create mqtt-password --value "<password>"
```