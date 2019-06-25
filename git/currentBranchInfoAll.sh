#!/bin/sh
function printBranch {
	cd $1
	{ git rev-parse --abbrev-ref HEAD ; echo "--" $1; } | tr "\n" " "
}

current=$(pwd)
gitRepos=$(find . -name ".git" -type d | sed 's/\/.git//')

for dir in $gitRepos
do
	printBranch $dir
	echo
	cd $current
done
