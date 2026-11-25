#!/usr/bin/env python3
"""
Random Permutation Tool
Generates random permutations of input letters until a duplicate is found.
"""

import random
import sys
import argparse


def random_permute(input_str, max_attempts=None, show_progress=True, silent=False):
    """
    Generate random permutations until a duplicate is found.
    
    Args:
        input_str: String to permute
        max_attempts: Optional limit on attempts (None = unlimited)
        show_progress: Whether to print each permutation
        silent: Only show final result
    
    Returns:
        tuple: (attempts_made, duplicate_found, seen_permutations)
    """
    chars = list(input_str)
    seen = set()
    seen.add(input_str)  # Original counts as seen
    attempts = 0
    original = input_str
    
    if not silent:
        print(f"Input: '{input_str}'")
        print(f"Length: {len(chars)} characters")
        print(f"Unique chars: {len(set(chars))}")
        print("-" * 50)
    
    while True:
        if max_attempts and attempts >= max_attempts:
            if not silent:
                print(f"\nReached max attempts ({max_attempts})")
                print(f"Total unique permutations seen: {len(seen)}")
            break
            
        # Shuffle randomly
        random.shuffle(chars)
        permuted = ''.join(chars)
        attempts += 1
        
        if show_progress and not silent:
            print(f"{attempts:5d}: {permuted}")
        
        # Check for duplicate
        if permuted in seen:
            if not silent:
                print("-" * 50)
                print(f"🔄 DUPLICATE FOUND at attempt #{attempts}")
                print(f"   Permutation: '{permuted}'")
                print(f"   Total unique permutations seen: {len(seen)}")
            return attempts, True, seen
        
        seen.add(permuted)
        
        # Check if we looped back to original
        if permuted == original and attempts > 1:
            if not silent:
                print("-" * 50)
                print(f"🔁 LOOPED BACK TO ORIGINAL at attempt #{attempts}")
                print(f"   Total unique permutations seen: {len(seen)}")
            return attempts, True, seen
    
    return attempts, False, seen


def main():
    parser = argparse.ArgumentParser(
        description="Generate random permutations until a duplicate is found",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python random_permute.py abc
  python random_permute.py "hello" --max 100
  python random_permute.py abc --silent
  python random_permute.py abc --no-progress
        """
    )
    parser.add_argument("input", nargs="?", help="String to permute")
    parser.add_argument("-m", "--max", type=int, dest="max_attempts", 
                        help="Maximum attempts before stopping")
    parser.add_argument("-s", "--silent", action="store_true",
                        help="Only show final result")
    parser.add_argument("--no-progress", action="store_true",
                        help="Don't show each permutation")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    
    args = parser.parse_args()
    
    # Get input
    if args.input:
        input_str = args.input
    else:
        input_str = input("Enter letters/string to permute: ").strip()
    
    if not input_str:
        print("Error: No input provided!", file=sys.stderr)
        sys.exit(1)
    
    if args.seed:
        random.seed(args.seed)
    
    random_permute(
        input_str,
        max_attempts=args.max_attempts,
        show_progress=not args.no_progress,
        silent=args.silent
    )


if __name__ == "__main__":
    main()