import random as rn
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

warnings.simplefilter("ignore")


# Model Logic
def random_population(n_var, n_sol, lb, ub):
    pop = np.zeros((n_sol, n_var))
    for i in range(n_sol):
        pop[i, :] = np.random.uniform(lb, ub)

    return pop


def crossover(pop, crossover_rate):
    offspring = np.zeros((crossover_rate, pop.shape[1]))
    for i in range(int(crossover_rate / 2)):
        r1 = np.random.randint(0, pop.shape[0])
        r2 = np.random.randint(0, pop.shape[0])
        while r1 == r2:
            r1 = np.random.randint(0, pop.shape[0])
            r2 = np.random.randint(0, pop.shape[0])
        cutting_point = np.random.randint(1, pop.shape[1])
        offspring[2 * i, 0:cutting_point] = pop[r1, 0:cutting_point]
        offspring[2 * i, cutting_point:] = pop[r2, cutting_point:]
        offspring[2 * i + 1, 0:cutting_point] = pop[r2, 0:cutting_point]
        offspring[2 * i + 1, cutting_point:] = pop[r1, cutting_point:]

    return offspring


def mutation(pop, mutation_rate):
    offspring = np.zeros((mutation_rate, pop.shape[1]))
    for i in range(int(mutation_rate / 2)):
        r1 = np.random.randint(0, pop.shape[0])
        r2 = np.random.randint(0, pop.shape[0])
        while r1 == r2:
            r1 = np.random.randint(0, pop.shape[0])
            r2 = np.random.randint(0, pop.shape[0])
        cutting_point = np.random.randint(0, pop.shape[1])
        offspring[2 * i] = pop[r1]
        offspring[2 * i, cutting_point] = pop[r2, cutting_point]
        offspring[2 * i + 1] = pop[r2]
        offspring[2 * i + 1, cutting_point] = pop[r1, cutting_point]

    return offspring


def local_search(pop, lb, ub, n_sol, step_size):
    offspring = np.zeros((n_sol, pop.shape[1]))
    for i in range(n_sol):
        r1 = np.random.randint(0, pop.shape[0])
        chromosome = pop[r1, :]
        r2 = np.random.randint(0, pop.shape[1])
        chromosome[r2] += np.random.uniform(-step_size, step_size)
        if chromosome[r2] < lb[r2]:
            chromosome[r2] = lb[r2]
        if chromosome[r2] > ub[r2]:
            chromosome[r2] = ub[r2]

        offspring[i, :] = chromosome
    return offspring


def evaluation(pop, target_ls, reg_ls):
    fitness_values = np.zeros((pop.shape[0], 4))
    for i, x in enumerate(pop):
        for j in range(4):
            fitness_values[i, j] = reg_ls[j].predict(pop[None, i, :]) - target_ls[j]

    return fitness_values


def crowding_calculation(fitness_values):
    pop_size = len(fitness_values[:, 0])
    fitness_value_number = len(fitness_values[0, :])
    matrix_for_crowding = np.zeros((pop_size, fitness_value_number))
    normalized_fitness_values = (
        fitness_values - fitness_values.min(0)
    ) / fitness_values.ptp(0)

    for i in range(fitness_value_number):
        crowding_results = np.zeros(pop_size)
        crowding_results[0] = 1
        crowding_results[pop_size - 1] = 1
        sorted_normalized_fitness_values = np.sort(normalized_fitness_values[:, i])
        sorted_normalized_values_index = np.argsort(normalized_fitness_values[:, i])
        crowding_results[1 : pop_size - 1] = (
            sorted_normalized_fitness_values[2:pop_size]
            - sorted_normalized_fitness_values[0 : pop_size - 2]
        )
        re_sorting = np.argsort(sorted_normalized_values_index)
        matrix_for_crowding[:, i] = crowding_results[re_sorting]

    crowding_distance = np.sum(matrix_for_crowding, axis=1)

    return crowding_distance


def remove_using_crowding(fitness_values, number_solutions_needed):
    pop_index = np.arange(fitness_values.shape[0])
    crowding_distance = crowding_calculation(fitness_values)
    selected_pop_index = np.zeros(number_solutions_needed)
    selected_fitness_values = np.zeros(
        (number_solutions_needed, len(fitness_values[0, :]))
    )
    for i in range(number_solutions_needed):
        pop_size = pop_index.shape[0]
        solution_1 = rn.randint(0, pop_size - 1)
        solution_2 = rn.randint(0, pop_size - 1)
        if crowding_distance[solution_1] >= crowding_distance[solution_2]:
            selected_pop_index[i] = pop_index[solution_1]
            selected_fitness_values[i, :] = fitness_values[solution_1, :]
            pop_index = np.delete(pop_index, (solution_1), axis=0)
            fitness_values = np.delete(fitness_values, (solution_1), axis=0)
            crowding_distance = np.delete(crowding_distance, (solution_1), axis=0)
        else:
            selected_pop_index[i] = pop_index[solution_2]
            selected_fitness_values[i, :] = fitness_values[solution_2, :]
            pop_index = np.delete(pop_index, (solution_2), axis=0)
            fitness_values = np.delete(fitness_values, (solution_2), axis=0)
            crowding_distance = np.delete(crowding_distance, (solution_2), axis=0)

    selected_pop_index = np.asarray(selected_pop_index, dtype=int)

    return selected_pop_index


def pareto_front_finding(fitness_values, pop_index):
    pop_size = fitness_values.shape[0]
    pareto_front = np.ones(pop_size, dtype=bool)
    for i in range(pop_size):
        for j in range(pop_size):
            if all(fitness_values[j] <= fitness_values[i]) and any(
                fitness_values[j] < fitness_values[i]
            ):
                pareto_front[i] = 0
                break

    return pop_index[pareto_front]


def selection(pop, fitness_values, pop_size):
    pop_index_0 = np.arange(pop.shape[0])
    pop_index = np.arange(pop.shape[0])
    pareto_front_index = []

    while len(pareto_front_index) < pop_size:
        new_pareto_front = pareto_front_finding(
            fitness_values[pop_index_0, :], pop_index_0
        )
        total_pareto_size = len(pareto_front_index) + len(new_pareto_front)

        if total_pareto_size > pop_size:
            number_solutions_needed = pop_size - len(pareto_front_index)
            selected_solutions = remove_using_crowding(
                fitness_values[new_pareto_front], number_solutions_needed
            )
            new_pareto_front = new_pareto_front[selected_solutions]

        pareto_front_index = np.hstack((pareto_front_index, new_pareto_front))
        remaining_index = set(pop_index) - set(pareto_front_index)
        pop_index_0 = np.array(list(remaining_index))

    selected_pop = pop[pareto_front_index.astype(int)]

    return selected_pop


def train_random_forest_models(df, shuffle, random_state=1):
    reg_ls = []
    feature_df, kpov_df = df.iloc[:, :13], df.iloc[:, 13:]

    for kpov_col in kpov_df.columns:
        combined_df = feature_df.copy()
        combined_df["y"] = kpov_df[kpov_col].values
        if shuffle == True:
            combined_df = combined_df.sample(frac=1, random_state=1)
        train_X = combined_df.iloc[:, :-1]
        train_Y = combined_df["y"]

        print(f"Training model for '{kpov_col}'")

        reg = RandomForestRegressor(
            max_depth=20,
            min_samples_split=2,
            min_samples_leaf=1,
            n_estimators=100,
            random_state=1,
            max_features="sqrt",
        )
        reg.fit(train_X, train_Y)
        reg_ls.append(reg)

    return reg_ls


def run_genetic_algorithm(initial_population, target_ls, reg_ls, config):
    for k in range(config["maximum_generation"]):
        print("Running Genetic Algorithm. NSGA-II iteration: ", k)

        offspring_from_crossover = crossover(
            initial_population, config["rate_crossover"]
        )
        offspring_from_mutation = mutation(initial_population, config["rate_mutation"])
        offspring_from_local_search = local_search(
            initial_population,
            config["lb"],
            config["ub"],
            config["rate_local_search"],
            config["step_size"],
        )

        initial_population = np.append(
            initial_population, offspring_from_crossover, axis=0
        )
        initial_population = np.append(
            initial_population, offspring_from_mutation, axis=0
        )
        initial_population = np.append(
            initial_population, offspring_from_local_search, axis=0
        )

        fitness_values = evaluation(initial_population, target_ls, reg_ls)
        initial_population = selection(
            initial_population, fitness_values, config["pop_size"]
        )

    return initial_population[0, :]


def run_model(df: pd.DataFrame):
    config = {
        "shuffle": True,
        "n_var": 13,
        "lb": [-10, 20, 200, 10, 80, 80, 0.5, 40, 0, 5, 0, -0.1, 0],
        "ub": [0, 60, 550, 2000, 95, 95, 50, 60, 115, 25, 3600, 5, 95],
        "pop_size": 150,
        "rate_crossover": 20,
        "rate_mutation": 20,
        "rate_local_search": 10,
        "step_size": 0.1,
        "maximum_generation": 30,
        "target_ls": [260, 89, 92, 92],
    }

    # Drop NaN values
    df = df.dropna()

    # Check if there are enough values in the data set. At least 100 rows and 17 columns (13 inputs and 4 outputs).
    if df.shape[0] >= 100 and df.shape[1] == 17:
        print(f"Running Model with {df.shape[0]} rows")

        # Train models
        reg_ls = train_random_forest_models(df, shuffle=config["shuffle"])

        # Run model
        pop = random_population(
            config["n_var"], config["pop_size"], config["lb"], config["ub"]
        )

        selected_solution = run_genetic_algorithm(
            pop, config["target_ls"], reg_ls, config
        )

        selected_solution_output = [
            reg.predict(selected_solution[None, :])[0] for reg in reg_ls
        ]

        # Combine selected solution and selected solution output
        combined_values = list(selected_solution) + list(selected_solution_output)
        combined_dict = {f"{col}": val for col, val in zip(df.columns, combined_values)}

        print("Recommended Values", combined_dict)

        return combined_dict
