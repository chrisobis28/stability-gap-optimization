#!/usr/bin/env python3

# Standard libraries
import sys
import os
import numpy as np
import tqdm
import math
# Pytorch
import torch
from torch.nn import functional as F
from torchvision import datasets, transforms
# For visualization
from torchvision.utils import make_grid
import matplotlib.pyplot as plt
import copy
from scipy.ndimage import uniform_filter1d

# Expand the module search path to parent directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
# Load custom-written code
import utils
from visual import visual_plt
from eval.evaluate import test_acc
from models.classifier import Classifier
from data.manipulate import TransformedDataset


################## INITIAL SET-UP ##################

# Specify directories, and if needed create them
p_dir = "./store/paper_plots_gap_depth_PLARGE"
d_dir = "./store/data"
if not os.path.isdir(p_dir):
    print("Creating directory: {}".format(p_dir))
    os.makedirs(p_dir)
if not os.path.isdir(d_dir):
    os.makedirs(d_dir)
    print("Creating directory: {}".format(d_dir))

n_experiments = 10
################## STABILITY GAP OPTIMIZER VALUES ##################

# SGD - hyperparameters: lr, momentum
# optimizer_name = 'SGD'
# lr = 0.1
# momentum_value = 0.9
# titleOfGraph = f"'Incremental Joint Training' with Mini-Batch GD, {lr} learning rate, {momentum_value} Momentum"
# my_plot_name = f"LLARGE_sg_{optimizer_name}_{lr}lr_{momentum_value}m_NONRESET_{n_experiments}exp"

# NAG - hyperparameters: lr, momentum
# optimizer_name = 'NAG'
# lr = 0.1
# momentum_value = 0.7
# titleOfGraph = f"'Incremental Joint Training' with {optimizer_name}, {lr} learning rate, {momentum_value} Momentum"
# my_plot_name = f"GS_sg_{optimizer_name}_{lr}lr_{momentum_value}m_NONRESET_{n_experiments}exp"

# AdaGrad - hyperparameters: lr (optionally eps, momentum)
# optimizer_name = 'AdaGrad'
# lr = 0.01
# titleOfGraph = f"'Incremental Joint Training' with {optimizer_name}, {lr} learning rate"
# my_plot_name = f"RESET_GS_sg_{optimizer_name}_{lr}lr_NONRESET_{n_experiments}exp"

# # RMSprop - hyperparameters: lr, alpha (optionally eps, momentum)
optimizer_name = 'RMSprop'
lr = 0.001
alpha = 0.9
titleOfGraph = f"'Incremental Joint Training' with {optimizer_name}, {lr} learning rate, {alpha} alpha"
my_plot_name = f"RESET_GS_sg_{optimizer_name}_{lr}lr_{alpha}a_NONRESET_{n_experiments}exp"

# Adam - hyperparameters: lr,s beta1, beta2 (optionally eps)
optimizer_name = 'Adam'
lr = 0.001
beta1 = 0.9
beta2 = 0.99
titleOfGraph = f"'Incremental Joint Training' with {optimizer_name}, {lr} learning rate, ({beta1},{beta2}) betas"
my_plot_name = f"RESET_GS_{optimizer_name}_{lr}lr_{beta1}&{beta2}bet_NONRESET_{n_experiments}exp"

################## PDF ##################

# Open pdf for plotting
plot_name = my_plot_name
full_plot_name = "{}/{}.pdf".format(p_dir, plot_name)
pp = visual_plt.open_pdf(full_plot_name)
figure_list = []

################## CREATE TASK SEQUENCE ##################

## Download the MNIST dataset
print("\n\n " +' LOAD DATA '.center(70, '*'))
MNIST_trainset = datasets.MNIST(root='data/', train=True, download=True,
                                transform=transforms.ToTensor())
MNIST_testset = datasets.MNIST(root='data/', train=False, download=True,
                               transform=transforms.ToTensor())
config = {'size': 28, 'channels': 1, 'classes': 10}

# Set for each task the amount of rotation to use
rotations = [0, 50, 100, 150]

# Specify for each task the transformed train- and testset
n_tasks = len(rotations)
train_datasets = []
test_datasets = []
for rotation in rotations:
    print(f'Loading rotation {rotation} train dataset:')
    train_datasets.append(TransformedDataset(
        MNIST_trainset, transform=transforms.RandomRotation(degrees=(rotation,rotation)),
    ))
    print(f'Loading rotation {rotation} test dataset:')
    test_datasets.append(TransformedDataset(
        MNIST_testset, transform=transforms.RandomRotation(degrees=(rotation,rotation)),
    ))

# Visualize the different tasks
figure, axis = plt.subplots(1, n_tasks, figsize=(3*n_tasks, 4))
n_samples = 49
for task_id in range(len(train_datasets)):
    # Show [n_samples] examples from the training set for each task
    data_loader = torch.utils.data.DataLoader(train_datasets[task_id], batch_size=n_samples, shuffle=True)
    image_tensor, _ = next(iter(data_loader))
    image_grid = make_grid(image_tensor, nrow=int(np.sqrt(n_samples)), pad_value=1) # pad_value=0 would give black borders
    axis[task_id].imshow(np.transpose(image_grid.numpy(), (1,2,0)))
    axis[task_id].set_title("Task {}".format(task_id+1))
    axis[task_id].axis('off')
figure_list.append(figure)

################## SET UP THE MODEL ##################

print("\n\n " + ' DEFINE THE CLASSIFIER '.center(70, '*'))

# Specify the architectural layout of the network to use
fc_lay = 3        #--> number of fully-connected layers
fc_units = 400    #--> number of units in each hidden layer

# Define the model
model = Classifier(image_size=config['size'], image_channels=config['channels'], classes=config['classes'],
                   fc_layers=fc_lay, fc_units=fc_units, fc_bn=False)

# Print some model info to screen
utils.print_model_info(model)


################## TRAINING AND EVALUATION ##################

print('\n\n' + ' TRAINING + CONTINUAL EVALUATION '.center(70, '*'))

momentum_value = locals().get('momentum_value', 0)  # Default to 0 if not defined
alpha = locals().get('alpha', 0.99)  # Default to 0.99 if not defined
beta1 = locals().get('beta1', 0.9)  # Default to 0.9 if not defined
beta2 = locals().get('beta2', 0.999)  # Default to 0.999 if not defined

# Define a function to train a model, while also evaluating its performance after each iteration
def train_and_evaluate(model, trainset, iters, optimizer_name, lr, batch_size, testsets, optimizer,
                       test_size=512, performances=[], task_id=0):
    '''Function to train a [model] on a given [dataset],
    while evaluating after each training iteration on [testset].'''

    model.train()
    iters_left = 1
    progress_bar = tqdm.tqdm(range(1, iters+1))

    for iteration in range(1, iters+1):
        optimizer.zero_grad()

        # Collect data from [trainset] and compute the loss
        iters_left -= 1
        if iters_left==0:
            # Prepares the dataset, splits it into batches, shuffles, drops last samples if they do not form a complete batch
            data_loader = iter(torch.utils.data.DataLoader(trainset, batch_size=batch_size,
                                                           shuffle=True, drop_last=True, pin_memory=False))
            iters_left = len(data_loader)
        x, y = next(data_loader)
        y_hat = model(x)
        loss = torch.nn.functional.cross_entropy(input=y_hat, target=y, reduction='mean')

        # Calculate test accuracy (in %) with the current model 

        # RUNNING EXPERIMENTS WITH EVALUATION ON ALL TASKS
        accuracy_task_1 = -1
        for index in range(task_id):
            accuracy = 100*test_acc(model, testsets[index], test_size=test_size, verbose=False, batch_size=512)
            if index == 0:
                accuracy_task_1 = accuracy
            performances[index].append(accuracy)

        # RUNNING EXPERIMENTS WITH EVALUATION ONLY ON TASK 1
        # accuracy_task_1 = 100*test_acc(model, testsets[index], test_size=test_size, verbose=False, batch_size=512)

        # Take gradient step
        loss.backward()
        optimizer.step()
        progress_bar.set_description(
        '<CLASSIFIER> | Task 1 | training loss: {loss:.3} | test accuracy: {prec:.3}% |'
            .format(loss=loss.item(), prec=accuracy_task_1)
        )
        progress_bar.update(1)
    progress_bar.close()

# Specify the training parameters
iters = 500         #--> for how many iterations to train?
batch_size = 128    #--> size of mini-batches
test_size = 2000   #--> number of test samples to evaluate on after each iteration

# # Define a list to keep track of the performance on task 1 after each iteration
# performance_task1 = []
# final_performance_after_task1 = None
# final_performance_after_last_task = None

# # Iterate through the contexts
# for task_id in range(n_tasks):
#     current_task = task_id+1

#     # Concatenate the training data of all tasks so far
#     joint_dataset = torch.utils.data.ConcatDataset(train_datasets[:current_task])

#     # Determine the batch size to use
#     batch_size_to_use = current_task*batch_size

#     # Train
#     print('Training after arrival of Task {}:'.format(current_task))
#     train_and_evaluate(model, trainset=joint_dataset, iters=iters, optimizer_name=optimizer_name, lr=lr,
#                       batch_size=batch_size_to_use, testset=test_datasets[0],
#                       test_size=test_size, performance=performance_task1)

#     # Measure Final Performance after Task 1 is finished
#     if current_task == 1 :
#         final_performance_after_task1 = performance_task1[-1]
#     # Measure Final Performance after Task 3 is finished
#     elif current_task == n_tasks :
#         final_performance_after_last_task = performance_task1[-1]

################## Running n experiments and averaging results for conclusiveness #####################
all_performances_experiments = []

for experiment_id in range(n_experiments):
    print(f'Experiment number {experiment_id + 1}:')

    model_copy = copy.deepcopy(model)

    # current_experiment_performance_task1 = []
    current_experiment_performances = [[] for _ in range(n_tasks)] # Performances for all tasks

    optimizer = torch.optim.SGD(model_copy.parameters(), lr=lr)
    if optimizer_name == 'SGD':
        optimizer = torch.optim.SGD(model_copy.parameters(), lr=lr, momentum=momentum_value, nesterov=False)
    elif optimizer_name == 'NAG':
        optimizer = torch.optim.SGD(model_copy.parameters(), lr=lr, momentum=momentum_value, nesterov=True)
    elif optimizer_name == 'RMSprop':
        optimizer = torch.optim.RMSprop(model_copy.parameters(), lr=lr, alpha=alpha)
    elif optimizer_name == 'AdaGrad':
        optimizer = torch.optim.Adagrad(model_copy.parameters(), lr=lr)
    elif optimizer_name == 'Adam':
        optimizer = torch.optim.Adam(model_copy.parameters(), lr=lr, betas=(beta1, beta2))
    else:
        raise ValueError(f"Unknown optimizer: {optimizer_name}")

    for task_id in range(n_tasks):

        # if optimizer_name == 'SGD':
        #     optimizer = torch.optim.SGD(model_copy.parameters(), lr=lr, momentum=momentum_value, nesterov=False)
        # elif optimizer_name == 'NAG':
        #     optimizer = torch.optim.SGD(model_copy.parameters(), lr=lr, momentum=momentum_value, nesterov=True)
        # elif optimizer_name == 'RMSprop':
        #     optimizer = torch.optim.RMSprop(model_copy.parameters(), lr=lr, alpha=alpha)
        # elif optimizer_name == 'AdaGrad':
        #     optimizer = torch.optim.Adagrad(model_copy.parameters(), lr=lr)
        # elif optimizer_name == 'Adam':
        #     optimizer = torch.optim.Adam(model_copy.parameters(), lr=lr, betas=(beta1, beta2))
        # else:
        #     raise ValueError(f"Unknown optimizer: {optimizer_name}")

        current_task = task_id+1

        # Concatenate the training data of all tasks so far
        joint_dataset = torch.utils.data.ConcatDataset(train_datasets[:current_task])

        # Determine the batch size to use
        batch_size_to_use = current_task*batch_size

        # Train
        print('Training after arrival of Task {}:'.format(current_task))
        train_and_evaluate(model_copy, trainset=joint_dataset, iters=iters, optimizer_name=optimizer_name, lr=lr,
                        batch_size=batch_size_to_use, testsets=test_datasets, optimizer=optimizer,
                        test_size=test_size, performances=current_experiment_performances, task_id=current_task)

        # # Measure Final Performance after Task 1 is finished
        # if current_task == 1 :
        #     final_performance_after_task1 = performance_task1[-1]
        # # Measure Final Performance after Task 3 is finished
        # elif current_task == n_tasks :
        #     final_performance_after_last_task = performance_task1[-1]
    
    all_performances_experiments.append(current_experiment_performances)

first_task_performances = [experiment[0] for experiment in all_performances_experiments]
first_task_performances_np = np.array(first_task_performances)
average_performance = np.mean(first_task_performances_np, axis=0)
ddof = 1 if n_experiments > 1 else 0
std_err = np.std(first_task_performances_np, axis=0, ddof=ddof)/np.sqrt(n_experiments)

import pandas as pd

# Save mean and stderr for Task 1 to CSV
# momentum_tag = str(momentum_value).replace('.', '_')
# results_df = pd.DataFrame({
#     "iteration": np.arange(len(average_performance)),
#     "mean_perf": average_performance,
#     "stderr_perf": std_err
# })
# # results_path = f"./store/data/perf_momentum_{momentum_tag}.csv"
# # perf_adagrad.csv
# results_path = f"./store/data/perf_nag_momentum_0_9.csv"
# results_df.to_csv(results_path, index=False)
# print(f"Saved results to {results_path}")

########## Quantitative Metrics ##########

# FORG, ACC

FORG_values_experiments = []
ACC_values_experiments = []
min_accuracy_values_experiments = []
wc_acc_performances_experiments = []
# wf_max_10_experiments = []
# wf_max_100_experiments = []
# wp_max_10_experiments = []
# wp_max_100_experiments = []

wf10_wf100_wp10_wp100_experiments = []

tbp_sd_sr_gd = []
highlight = 0

for i in range(n_experiments):
    # Accuracies for Experiment i
    accuracies_experiment_i = all_performances_experiments[i]

    wf10_wf100_wp10_wp100_experiment_i = []

    final_FORG_values_experiment_i = []
    final_ACC_values_experiment_i = []
    min_ACC_values_experiment_i = []
    tbp_sd_sr_gd_experiment_i = []

    ############################# TBP SD SR #############################

    for task_id in range(n_tasks - 1):
        performances_task = accuracies_experiment_i[task_id]
        print(len(performances_task))
        tbp_task = []
        sd_task = []
        sr_task = []
        sr_fixed_task = []
        gd_task = []

        # WINDOWS
        for j in range(n_tasks - task_id - 1):
            # Index of Pre-Task Performance
            baseline_index = iters * (j+1) - 1
            # First Index of the window
            start_index = iters * (j+1)
            # Index right after window
            end_index_exclusive = start_index + iters

            # Smooth Window to Account for Anomalies
            window = performances_task[start_index:end_index_exclusive]
            smoothed_window = uniform_filter1d(window, size=5)

            min_window_index = np.argmin(smoothed_window)
            # min_index = start_index + min_rel_index

            smoothed_baseline = np.mean(performances_task[baseline_index - 4 : baseline_index + 1])

            window_baseline_index = -1
            recovery_window_index = None

            for i in range(min_window_index, iters):
                if smoothed_window[i] >= smoothed_baseline:
                    recovery_window_index = i
                    break
            tbp_task.append(i)

            if recovery_window_index is None:
                recovery_window_index = iters-1

            distance_to_minimum_in_window = min_window_index - window_baseline_index

            if min_window_index >= 0:
                slope_down = (smoothed_window[min_window_index] - smoothed_baseline) / distance_to_minimum_in_window
            else:
                slope_down = np.nan
            sd_task.append(slope_down)
            
            if recovery_window_index is not None:
                slope_up = (smoothed_window[recovery_window_index] - smoothed_window[min_window_index]) / (recovery_window_index - min_window_index)
            else:
                slope_up = np.nan
            sr_task.append(slope_up)

            gd_task.append(smoothed_baseline - window[min_window_index])

            new_recovery_index = min(2 * distance_to_minimum_in_window, len(window) - 1)
            # sr_fixed_task.append((window[min_window_index+30] - window[min_window_index]) / 30)
            # fixed_delta = 30  # or use distance_to_minimum_in_window
            # fixed_recovery_index = min(min_window_index + fixed_delta, len(window) - 1)
            highlight = new_recovery_index

            # if new_recovery_index > min_window_index:
            #     sr_fixed = (smoothed_window[new_recovery_index] - smoothed_window[min_window_index]) / (distance_to_minimum_in_window)
            # else:
            #     sr_fixed = np.nan

            if new_recovery_index <= recovery_window_index:
                sr_fixed = (smoothed_window[new_recovery_index] - smoothed_window[min_window_index]) / (distance_to_minimum_in_window)
            else:
                sr_fixed = (smoothed_window[recovery_window_index] - smoothed_window[min_window_index]) / (recovery_window_index - min_window_index)

            sr_fixed_task.append(sr_fixed)

            print(f'For Task {task_id + 1}, Window {j+1}\n')
            print(f'Min index{min_window_index} with value {smoothed_window[min_window_index]}\n')
            print(f'Recovery index{recovery_window_index} with value {smoothed_window[recovery_window_index]}\n')
            print(f'Recovery slope {slope_up}\n')
            print(f'Fixed recovery index{new_recovery_index} with value {smoothed_window[new_recovery_index]}\n')
            print(f'Fixed recovery slope {sr_fixed}\n')

            # print(f'Performance at baseline_index is {performances_task[baseline_index]}, min_index is {window[min_window_index]} and recovery_index is {window[recovery_window_index]}')
        
        print(sr_fixed_task)
        print([np.nanmean(tbp_task), np.nanmean(sd_task), np.nanmean(sr_task), np.nanmean(gd_task), np.nanmean(sr_fixed_task)])
        tbp_sd_sr_gd_experiment_i.append([np.nanmean(tbp_task), np.nanmean(sd_task), np.nanmean(sr_task), np.nanmean(gd_task), np.nanmean(sr_fixed_task)])
    
    tbp_sd_sr_gd.append(np.mean(tbp_sd_sr_gd_experiment_i, axis=0))

    ############################# TBP SD SR #############################

    for j in range(n_tasks-1):
        # FORG - difference between final performance and performance after task training finished
        final_FORG_values_experiment_i.append(accuracies_experiment_i[j][iters - 1] - accuracies_experiment_i[j][-1])
        # ACC - final ACC of each task
        final_ACC_values_experiment_i.append(accuracies_experiment_i[j][-1])
        min_ACC_values_experiment_i.append(min(accuracies_experiment_i[j][iters:]))
    
    final_ACC_values_experiment_i.append(accuracies_experiment_i[n_tasks-1][-1])
    ACC_values_experiments.append(np.mean(final_ACC_values_experiment_i))

    FORG_values_experiments.append(np.mean(final_FORG_values_experiment_i))

    # min-ACC as average of all task's minimum values since training finishes
    min_accuracy_values_experiments.append(np.mean(min_ACC_values_experiment_i))

    # WC-ACC takes the previous min-ACC (average of minimums) and averages it with the final performance on the final task
    wc_acc_performances_experiments.append(np.mean(min_ACC_values_experiment_i) * (1 - 1 / n_tasks) + accuracies_experiment_i[n_tasks-1][-1] * (1 / n_tasks))

    for j in range(n_tasks-1):
        wf_max_10 = -sys.maxsize - 1
        wf_max_100 = -sys.maxsize - 1
        wp_max_10 = -sys.maxsize - 1
        wp_max_100 = -sys.maxsize - 1
        array_to_compare_with_wf_10 = [accuracies_experiment_i[j][500]] # Window of 10 than can be used for both WF and WP
        array_to_compare_with_wf_100 = [accuracies_experiment_i[j][500]] # Window of 100 than can be used for both WF and WP

        for current_accuracy in accuracies_experiment_i[j][501:]:
            wf_max_10 = max(wf_max_10, max((x - current_accuracy) for x in array_to_compare_with_wf_10))
            wf_max_100 = max(wf_max_100, max((x - current_accuracy) for x in array_to_compare_with_wf_100))
            wp_max_10 = max(wp_max_10, max((current_accuracy - x) for x in array_to_compare_with_wf_10))
            wp_max_100 = max(wp_max_100, max((current_accuracy - x) for x in array_to_compare_with_wf_100))
            array_to_compare_with_wf_10.append(current_accuracy)
            array_to_compare_with_wf_100.append(current_accuracy)

            if len(array_to_compare_with_wf_10) == 11:
                array_to_compare_with_wf_10 = array_to_compare_with_wf_10[1:]
            if len(array_to_compare_with_wf_100) == 101:
                array_to_compare_with_wf_100 = array_to_compare_with_wf_100[1:]

        wf10_wf100_wp10_wp100_experiment_i.append([wf_max_10, wf_max_100, wp_max_10, wp_max_100])
        # 1 experiment testing
        print(f'Task {j+1}')
        print([wf_max_10, wf_max_100, wp_max_10, wp_max_100])


    wf10_wf100_wp10_wp100_experiments.append(np.mean(wf10_wf100_wp10_wp100_experiment_i, axis=0))
    # wf_max_10_experiments.append(wf_max_10)
    # wf_max_100_experiments.append(wf_max_100)
    # wp_max_10_experiments.append(wp_max_10)
    # wp_max_100_experiments.append(wp_max_100)

qm_forg = round(np.mean(FORG_values_experiments), 2)
qm_forg_sd = round(np.std(FORG_values_experiments, ddof=ddof)/np.sqrt(n_experiments), 2)
qm_acc = round(np.mean(ACC_values_experiments), 2)
qm_acc_sd = round(np.std(ACC_values_experiments, ddof=ddof)/np.sqrt(n_experiments), 2)
qm_min_acc = round(np.mean(min_accuracy_values_experiments), 2)
qm_min_acc_sd = round(np.std(min_accuracy_values_experiments, ddof=ddof)/np.sqrt(n_experiments), 2)
qm_wc_acc = round(np.mean(wc_acc_performances_experiments), 2)
qm_wc_acc_std = round(np.std(wc_acc_performances_experiments, ddof=ddof)/np.sqrt(n_experiments), 2)

mean_windowed = np.mean(wf10_wf100_wp10_wp100_experiments, axis=0)
sd_windowed = np.std(wf10_wf100_wp10_wp100_experiments, axis=0, ddof=ddof)/np.sqrt(n_experiments)

mean_tbp_sd_sr = np.mean(tbp_sd_sr_gd, axis=0)
sd_tbp_sd_sr = np.std(tbp_sd_sr_gd, axis=0, ddof=ddof)/np.sqrt(n_experiments)

qm_wf10 = round(mean_windowed[0], 2)
qm_wf10_std = round(sd_windowed[0], 2)
qm_wf100 = round(mean_windowed[1], 2)
qm_wf100_std = round(sd_windowed[1], 2)
qm_wp10 = round(mean_windowed[2], 2)
qm_wp10_std = round(sd_windowed[2], 2)
qm_wp100 = round(mean_windowed[3], 2)
qm_wp100_std = round(sd_windowed[3], 2)

qm_tbp = round(mean_tbp_sd_sr[0], 1)
qm_tbp_std = round(sd_tbp_sd_sr[0], 1)
qm_gd = round(mean_tbp_sd_sr[3], 2)
qm_gd_std = round(sd_tbp_sd_sr[3], 2)
qm_sd = round(mean_tbp_sd_sr[1], 3)
qm_sd_std = round(sd_tbp_sd_sr[1], 3)
qm_sr = round(mean_tbp_sd_sr[2], 3)
qm_sr_std = round(sd_tbp_sd_sr[2], 3)
qm_sr_fixed = round(mean_tbp_sd_sr[4], 3)
qm_sr_fixed_std = round(sd_tbp_sd_sr[4], 3)

# qm_decline_slope = (lowest_point_after_task_1 - average_performance[iters-1]) / (average_performance.index(lowest_point_after_task_1)- (iters - 1))
# qm_incline_slope = (next_point_index - lowest_accuracy)
# qm_tlp
# qm_tsd

################## PLOTTING ##################

# ## Plot per-iteration performance curve
# # performance_graph = visual_plt.plot_lines(
# #     [average_performance], x_axes=list(range(n_tasks*iters)),
# #     line_names=['Incremental Joint'],
# #     title=titleOfGraph,
# #     ylabel="Test Accuracy (%) on Task 1",
# #     xlabel="Total number of training iterations", figsize=(10,5),
# #     v_line=[iters*(i+1) for i in range(n_tasks-1)], v_label='Task switch', ylim=(0,100)
# # )

# plt.figure(figsize=(10, 5))
# line, = plt.plot(average_performance, label='Average Performance', color='#FFA500', linewidth=1)
# plt.fill_between(
#     range(len(average_performance)),
#     average_performance - std_err,  # lwb
#     average_performance + std_err,  # upb
#     color='red',  # light orange
#     alpha=0.3,
#     label='Standard Error'
# )

# for task_switch in range(1, n_tasks):
#     plt.axvline(x=iters*task_switch, color='gray', linestyle='--', label='Task switch' if task_switch == 1 else "")

# plt.ylim(70,100)
# plt.title(titleOfGraph)
# plt.xlabel("Total number of training iterations")
# plt.ylabel("Test Accuracy (%) on Task 1")

# plt.legend()

# figure_list.append(plt.gcf())

################## PLOTTING ##################

padded_avg_list = []
padded_stderr_list = []

colors = ['#FFA500', '#1f77b4', '#2ca02c', '#d62728']

for task_id in range(n_tasks):
    task_performances = [experiment[task_id] for experiment in all_performances_experiments]
    task_performances_np = np.array(task_performances)

    average_performance = np.mean(task_performances_np, axis=0)
    ddof = 1 if n_experiments > 1 else 0
    std_err = np.std(task_performances_np, axis=0, ddof=ddof) / np.sqrt(n_experiments)

    total_iters = iters * n_tasks
    full_range = np.arange(total_iters)

    padded_avg = np.full(total_iters, np.nan)
    padded_stderr = np.full(total_iters, np.nan)
    padded_avg[iters * task_id :] = average_performance
    padded_stderr[iters * task_id :] = std_err

    padded_avg_list.append(padded_avg)
    padded_stderr_list.append(padded_stderr)

    plt.figure(figsize=(10, 5))
    plt.plot(full_range, padded_avg, label=f'Task {task_id + 1}', color=colors[task_id % len(colors)], linewidth=1)
    plt.fill_between(
        full_range,
        padded_avg - padded_stderr,
        padded_avg + padded_stderr,
        color='red',
        alpha=0.3,
        label='Standard Error'
    )

    for switch_id in range(1, n_tasks):
        plt.axvline(x=iters * switch_id, color='gray', linestyle='--', label='Task switch' if switch_id == 1 else "")

    plt.xlim(0, total_iters)
    plt.ylim(70, 100)
    plt.title(f"{titleOfGraph} — Task {task_id + 1}")
    plt.xlabel("Total number of training iterations")
    plt.ylabel(f"Test Accuracy (%) on Task {task_id + 1}")
    plt.legend()

    figure_list.append(plt.gcf())

plt.figure(figsize=(12, 6))
x_range = np.arange(iters * n_tasks)

for task_id in range(n_tasks):
    padded_avg = padded_avg_list[task_id]
    padded_stderr = padded_stderr_list[task_id]

    plt.plot(x_range, padded_avg, label=f'Task {task_id + 1}', linewidth=1, color=colors[task_id % len(colors)])
    plt.fill_between(
        x_range,
        padded_avg - padded_stderr,
        padded_avg + padded_stderr,
        color='red',
        alpha=0.3,
        label='Standard Error' if task_id == n_tasks-1 else None
    )

for switch_id in range(1, n_tasks):
    plt.axvline(x=iters * switch_id, color='gray', linestyle='--', label='Task switch' if switch_id == 1 else "")

# plt.axvline(x=499+highlight, color='gray', linestyle='--')

plt.xticks(range(0,2001,500))
plt.yticks(range(70, 101, 5))
plt.ylim(70, 100)
plt.xlim(0, 2000)


plt.tick_params(axis='x', labelsize=19)

plt.tick_params(axis='y', labelsize=19)

# plt.title(f"{titleOfGraph} — All Tasks")
plt.xlabel("Iterations", fontsize=18)
plt.ylabel("Test Accuracy (%)", fontsize=18)
plt.legend(loc='lower right', frameon=True, facecolor='#f2f2f2', edgecolor='#e0e0e0', fontsize=10, framealpha=1, borderpad=1)
figure_list.append(plt.gcf())

text_figure = plt.figure(figsize=(10,4))
plt.axis('off')

text = f"Runs: {n_experiments}\n"
if optimizer_name == 'SGD':
    text += f"Optimizer: Mini-Batch GD\n"
else:
    text += f"Optimizer: {optimizer_name}\n"
text += f"Learning Rate: {lr}\n"
if optimizer_name in ['SGD', 'NAG']:
    text += f"Momentum: {momentum_value}\n"
elif optimizer_name == 'RMSprop':
    text += f"Alpha: {alpha}\n"
elif optimizer_name == 'Adam':
    text += f"Betas: ({beta1},{beta2})\n"

text += f"FORG: {qm_forg} (+/-{qm_forg_sd})\n"
text += f"ACC: {qm_acc} (+/-{qm_acc_sd})\n"
text += f"min-ACC: {qm_min_acc} (+/-{qm_min_acc_sd})\n"
text += f"WC-ACC: {qm_wc_acc} (+/-{qm_wc_acc_std})\n"
text += f"WF10: {qm_wf10} (+/-{qm_wf10_std})\n"
text += f"WF100: {qm_wf100} (+/-{qm_wf100_std})\n"
text += f"WP10: {qm_wp10} (+/-{qm_wp10_std})\n"
text += f"WP100: {qm_wp100} (+/-{qm_wp100_std})\n"
text += f"TBP: {qm_tbp} (+/-{qm_tbp_std})\n"
text += f"GD: {qm_gd} (+/-{qm_gd_std})\n"
text += f"SD: {qm_sd} (+/-{qm_sd_std})\n"
text += f"SRfixed: {qm_sr_fixed} (+/-{qm_sr_fixed_std})\n"
text += f"SRfull: {qm_sr} (+/-{qm_sr_std})"

plt.text(0.5, 0.5, text, ha='center', va='center', fontsize=12, color='black')

figure_list.append(text_figure)

## Finalize the pdf with the plots
# -add figures to pdf
for figure in figure_list:
    pp.savefig(figure)
# -close pdf
pp.close()
# -print name of generated plot on screen
print("\nGenerated plot: {}\n".format(full_plot_name))

