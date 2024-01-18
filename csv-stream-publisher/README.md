# CSV Stream Publisher
This application ingests Data from a CSV file and publishes it to the Kelvin platform.

# Requirements
1. Python 3.8 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Install project dependencies: `pip3 install -r requirements.txt`
4. Docker (optional) for upload the application to a Kelvin Instance.

# Usage
1. Build/Replace the CSV file:
   1. File path & name must be `csv/data.csv`
   2. `timestamp` column is optional
   3. Each column relates to a Datastream

    Example:
    ```xls
    motor_torque,motor_temperature,motor_speed_set_point,tubing_pressure,gas_flow_rate
    214.5436355,95.02338108,2000,20851.36638,0.7944452821
    358.4114549,86.97313917,2000,22102.36742,0.2205483025
    627.1962679,86.97313917,2000,25132.58449,0.2374164278
    ...
    ```

2. Add the Output Datastreams to the `app.yaml`:
    ```yaml
    app:
      type: kelvin
      kelvin:
        outputs:
        - data_type: number
            name: motor_torque
        - data_type: number
            name: motor_temperature
        - data_type: number
            name: motor_speed_set_point
        - data_type: number
            name: tubing_pressure
        - data_type: number
            name: gas_flow_rate
    ...
    ```
3. Configure publish rate of each row (in seconds) and replay upon end of file (boolean) on the `app.yaml`:
    ```yaml
    app:
      type: kelvin
      kelvin:
        configuration:
          publish_rate: 30
          replay: True
    ...
    ```

4. Bump the application version on the `app.yaml`:
    ```yaml
    info:
      name: csv-stream-publisher
      title: CSV Stream Publisher
      version: 1.0.1
      description: Ingests and Publishes CSV Data
    ...
    ```

5. Upload the new version: `kelvin app upload`

6. Go to the UI (`{INSTANCE_URL}/core/applications/csv-stream-publisher/management`) and `Add Assets`