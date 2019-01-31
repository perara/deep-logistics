# logistics-sim
A Simulator for complex logistic environments


## Installation
```python
pip install perara-deep-logistics
```
## Run using docker
To increase sample throughput, the docker containers use a shared volume
to store experience replay packs. These packs can then be loaded as training 
samples for the rl-algorithm (Typically inside another container with access
to the same volume)
```
docker volume create --name deep-logistics
```
To run a single agent, run the following command:
```
docker run -d --volume deep-logistics perara/deep-logistics
```

## Environment Specifications

### Constrains and Assumptions
* The agent cannot select direction actions unless standing still
* Assumes the grid to have quadratic cells.
