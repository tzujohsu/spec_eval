#%%
import time
import random
import torch
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForCausalLM
from autoregressive_sampling import autoregressive_sampling
from speculative_sampling import speculative_sampling
from utils import load_data
import numpy as np
import argparse
from BiLD import BiLD


#%%

def parse_arguments():
    parser = argparse.ArgumentParser(description='args for main.py')

    parser.add_argument('--approx_model_name', type=str, default="double7/vicuna-68m")
    parser.add_argument('--target_model_name', type=str, default="lmsys/vicuna-7b-v1.3")
    parser.add_argument('--seed', '-s', type=int, default=None, help='set a random seed, which can makes the result reproducible')
    parser.add_argument('--max_tokens', '-M', type=int, default=30, help='max token number generated.')
    parser.add_argument('--gamma', '-g', type=int, default=4, help='# guess time.')
    parser.add_argument('--temperature', '-t', type=int, default=0, help='temperature')
    parser.add_argument('--datafile_path', '-f', type=str, default='combined_data.jsonl', help='temperature')
    parser.add_argument('--output_dir', '-o', type=str, default='/outputs', help='output-txt')
    parser.add_argument('--subtask_result', type=bool, default=True, help='whether print out subtask result')
    parser.add_argument('--SPS', type=bool, default=True, help='whether to run Speculative Sampling')
    parser.add_argument('--BiLD', type=bool, default=False, help='whether to run BiLD')
    parser.add_argument('--fallback_thres', type=float, default=0.6, help='fallback threshold for BiLD')
    parser.add_argument('--rollback_thres', type=int, default=3, help='rollback threshold for BiLD')
    args = parser.parse_args()
    return args


#%%

args = parse_arguments()
device = "cuda" if torch.cuda.is_available() else "cpu"

target_model = AutoModelForCausalLM.from_pretrained(args.target_model_name).to(device)
draft_model = AutoModelForCausalLM.from_pretrained(args.approx_model_name).to(device)
tokenizer = AutoTokenizer.from_pretrained(args.target_model_name)
print("finished loading model")

print("Target Model:", target_model.config._name_or_path)
print("Approx Model:", draft_model.config._name_or_path)
print()

#%%
jsonl_file = args.datafile_path
texts, subtasks = load_data(jsonl_file)
MAX_NEW_TOKENS = args.max_tokens
TEMPERATURE = args.temperature
inputs_sample = tokenizer(texts[-1], return_tensors="pt").to(device)
sub_result_sps = {'multi-turn':[0,0], 'translation':[0,0], 'summarization':[0,0], 'qa':[0,0], 'math_reasoning':[0,0], 'rag':[0,0], 'law analytics':[0,0], 'grammar correction':[0,0]}
sub_result_as = {'multi-turn':[0,0], 'translation':[0,0], 'summarization':[0,0], 'qa':[0,0], 'math_reasoning':[0,0], 'rag':[0,0], 'law analytics':[0,0], 'grammar correction':[0,0]}
sub_result_bild = {'multi-turn':[0,0], 'translation':[0,0], 'summarization':[0,0], 'qa':[0,0], 'math_reasoning':[0,0], 'rag':[0,0], 'law analytics':[0,0], 'grammar correction':[0,0]}

print("finished loading data")
print("# data instances: ",len(texts))
print()

# check_function(target_model, draft_model, tokenizer, inputs_sample, MAX_NEW_TOKENS, autoregressive_sampling, speculative_sampling)
# print("function check done")


# %% 
print("Benchmarking naive Autoregressive Sampling...")
## Autoregressive
# Warmup
tokens = autoregressive_sampling(target_model, initial_prompt_seq=inputs_sample.input_ids, 
                                 target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), temperature=TEMPERATURE)

time_taken = 0
new_tokens = 0
for i in tqdm(range(len(texts))):
# for i in tqdm(range(81+80, 81+80+80)):
  if subtasks[i] == 'multi-turn':
    turns = texts[i][0]
    tmp_new_tokens, tmp_time_taken = 0, 0
    # turn 1
    inputs = tokenizer(turns[0], return_tensors="pt").to(device)
    start_len = len(inputs.input_ids)

    start_time = time.time_ns()
    tokens = autoregressive_sampling(target_model, initial_prompt_seq=inputs.input_ids, 
                                     target_len=MAX_NEW_TOKENS+len(inputs.input_ids), temperature=TEMPERATURE)
    end_time = time.time_ns()
    new_tokens += len(tokens[0]) - start_len
    time_taken += (end_time - start_time) / 1_000_000_000
    tmp_new_tokens += len(tokens[0]) - start_len
    tmp_time_taken += (end_time - start_time) / 1_000_000_000

    # turn 2
    response = tokenizer.decode(tokens[0], skip_special_tokens=True)
    response += (" " + turns[1])
    inputs = tokenizer(response, return_tensors="pt").to(device)
    start_len = len(inputs.input_ids)
    start_time = time.time_ns()
    tokens = autoregressive_sampling(target_model, initial_prompt_seq=inputs.input_ids, 
                                     target_len=MAX_NEW_TOKENS+len(inputs.input_ids), temperature=TEMPERATURE)
    end_time = time.time_ns()

    new_tokens += len(tokens[0]) - start_len
    time_taken += (end_time - start_time) / 1_000_000_000
    tmp_new_tokens += len(tokens[0]) - start_len
    tmp_time_taken += (end_time - start_time) / 1_000_000_000

    sub_result_as[subtasks[i]][0]+= (tmp_new_tokens)
    sub_result_as[subtasks[i]][1]+= (tmp_time_taken)
  else:
    text = texts[i]
    inputs = tokenizer(text, return_tensors="pt").to(device)
    start_len = len(inputs.input_ids)

    start_time = time.time_ns()
    tokens = autoregressive_sampling(target_model, initial_prompt_seq=inputs.input_ids, 
                                     target_len=MAX_NEW_TOKENS+len(inputs.input_ids), temperature=TEMPERATURE)
    end_time = time.time_ns()

    new_tokens += len(tokens[0]) - start_len
    time_taken += (end_time - start_time) / 1_000_000_000
    sub_result_as[subtasks[i]][0]+= (len(tokens[0]) - start_len)
    sub_result_as[subtasks[i]][1]+= (((end_time - start_time) / 1_000_000_000))

print(f"Throughput (Autoregressive Sampling): {new_tokens/time_taken:.2f} tok/s")
overall_result_as = new_tokens/time_taken

#%%
## Speculative Sampling
# Warmup
if args.SPS == True:
    print("Benchmarking Speculative Sampling...")
    tokens = speculative_sampling(target_model, draft_model, prefix=inputs_sample.input_ids, 
                                target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), tokenizer=tokenizer, temperature=TEMPERATURE)

    time_taken = 0
    new_tokens = 0
    for i in tqdm(range(len(texts))):
    
        if subtasks[i] == 'multi-turn':
            turns = texts[i][0]
            tmp_new_tokens, tmp_time_taken = 0, 0
            # turn 1
            inputs = tokenizer(turns[0], return_tensors="pt").to(device)
            start_len = len(inputs.input_ids)

            start_time = time.time_ns()
            tokens = speculative_sampling(target_model, draft_model, prefix=inputs.input_ids,  
                                        target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), temperature=TEMPERATURE, tokenizer=tokenizer)
            end_time = time.time_ns()
            new_tokens += len(tokens[0]) - start_len
            time_taken += (end_time - start_time) / 1_000_000_000
            tmp_new_tokens += len(tokens[0]) - start_len
            tmp_time_taken += (end_time - start_time) / 1_000_000_000

            # turn 2
            response = tokenizer.decode(tokens[0], skip_special_tokens=True)
            response += (" " + turns[1])
            inputs = tokenizer(response, return_tensors="pt").to(device)
            start_len = len(inputs.input_ids)
            start_time = time.time_ns()
            tokens = speculative_sampling(target_model, draft_model, prefix=inputs.input_ids,  
                                        target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), temperature=TEMPERATURE, tokenizer=tokenizer)
            end_time = time.time_ns()

            new_tokens += len(tokens[0]) - start_len
            time_taken += (end_time - start_time) / 1_000_000_000
            tmp_new_tokens += len(tokens[0]) - start_len
            tmp_time_taken += (end_time - start_time) / 1_000_000_000

            sub_result_sps[subtasks[i]][0]+= (tmp_new_tokens)
            sub_result_sps[subtasks[i]][1]+= (tmp_time_taken)
        else:
            text = texts[i]
            inputs = tokenizer(text, return_tensors="pt").to(device)
            start_len = len(inputs.input_ids)

            start_time = time.time_ns()
            tokens = speculative_sampling(target_model, draft_model, prefix=inputs.input_ids,  
                                        target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), temperature=TEMPERATURE, tokenizer=tokenizer)
            end_time = time.time_ns()

            new_tokens += len(tokens[0]) - start_len
            time_taken += (end_time - start_time) / 1_000_000_000
            sub_result_sps[subtasks[i]][0]+= (len(tokens[0]) - start_len)
            sub_result_sps[subtasks[i]][1]+= (((end_time - start_time) / 1_000_000_000))

    overall_result_sps = new_tokens/time_taken
    print(f"Throughput (Speculative Sampling): {new_tokens/time_taken:.2f} tok/s")



#%%
if args.BiLD == True:
    print("Benchmarking BiLD...")
    tokens = BiLD(target_model, draft_model, prefix=inputs.input_ids, target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), 
                            temperature=TEMPERATURE, fallback_threshold=args.fallback_thres, rollback_threshold=args.rollback_thres)
    print("warm up done")
    time_taken = 0
    new_tokens = 0
    for i in tqdm(range(len(texts))):
    # for i in tqdm(range(81+80, 81+80+80)):

        if subtasks[i] == 'multi-turn':
            turns = texts[i][0][:200]
            tmp_new_tokens, tmp_time_taken = 0, 0
            # turn 1
            inputs = tokenizer(turns[0], return_tensors="pt").to(device)
            start_len = len(inputs.input_ids)

            start_time = time.time_ns()
            tokens = BiLD(target_model, draft_model, prefix=inputs.input_ids, target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), 
                            temperature=TEMPERATURE, fallback_threshold=args.fallback_thres, rollback_threshold=args.rollback_thres)
            end_time = time.time_ns()
            new_tokens += len(tokens[0]) - start_len
            time_taken += (end_time - start_time) / 1_000_000_000
            tmp_new_tokens += len(tokens[0]) - start_len
            tmp_time_taken += (end_time - start_time) / 1_000_000_000

            # turn 2
            response = tokenizer.decode(tokens[0], skip_special_tokens=True)
            response += (" " + turns[1])
            inputs = tokenizer(response, return_tensors="pt").to(device)
            start_len = len(inputs.input_ids)
            start_time = time.time_ns()
            tokens = BiLD(target_model, draft_model, prefix=inputs.input_ids, target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), 
                            temperature=TEMPERATURE, fallback_threshold=args.fallback_thres, rollback_threshold=args.rollback_thres)
            end_time = time.time_ns()

            new_tokens += len(tokens[0]) - start_len
            time_taken += (end_time - start_time) / 1_000_000_000
            tmp_new_tokens += len(tokens[0]) - start_len
            tmp_time_taken += (end_time - start_time) / 1_000_000_000

            sub_result_bild[subtasks[i]][0]+= (tmp_new_tokens)
            sub_result_bild[subtasks[i]][1]+= (tmp_time_taken)
        else:
            text = texts[i]
            inputs = tokenizer(text, return_tensors="pt").to(device)
            start_len = len(inputs.input_ids)

            start_time = time.time_ns()
            tokens = BiLD(target_model, draft_model, prefix=inputs.input_ids, target_len=MAX_NEW_TOKENS+len(inputs_sample.input_ids), 
                            temperature=TEMPERATURE, fallback_threshold=args.fallback_thres, rollback_threshold=args.rollback_thres)
            end_time = time.time_ns()

            new_tokens += len(tokens[0]) - start_len
            time_taken += (end_time - start_time) / 1_000_000_000
            sub_result_bild[subtasks[i]][0]+= (len(tokens[0]) - start_len)
            sub_result_bild[subtasks[i]][1]+= (((end_time - start_time) / 1_000_000_000))

    overall_result_biLD = new_tokens/time_taken
    print(f"Throughput (BiLD): {new_tokens/time_taken:.2f} tok/s")

#%%
# print out all results

print("================")
if args.SPS:
    print(f"Overall Result: AS: {round(overall_result_as, 2)} tokens/sec, SPS: {round(overall_result_sps,2)} \
        tokens/sec -> {round((overall_result_sps/overall_result_as), 2)} X Speedup")

if args.BiLD:
    print(f"Overall Result: AS: {round(overall_result_as, 2)} tokens/sec, BiLD: {round(overall_result_biLD,2)} \
        tokens/sec -> {round((overall_result_biLD/overall_result_as), 2)} X Speedup")

print("==================")

if args.subtask_result:
  print("Subtask Result: ")
  
  for i in sub_result_as:
    
    AS = round((sub_result_as[i][0]/sub_result_as[i][1]),2)
    if args.SPS:
        SPS = round((sub_result_sps[i][0]/sub_result_sps[i][1]),2)
        print(f"Subtask: {i}, AS: {AS} tokens/sec, SPS: {SPS} tokens/sec -> {(SPS/AS)} X Speedup")
    if args.BiLD:
        BILD = round((sub_result_bild[i][0]/sub_result_bild[i][1]),2)
        print(f"Subtask: {i}, AS: {AS} tokens/sec, BiLD: {BILD} tokens/sec -> {(BILD/AS)} X Speedup")
    print()
