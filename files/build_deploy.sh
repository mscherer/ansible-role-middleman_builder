#!/bin/bash
export PATH="/usr/local/bin:/srv/builder/bin:$PATH"
NAME=$1
BRANCH=$2
EMAIL_ERROR=$3
# keep remote last, since that's a optional argument
REMOTE=$4

if [ -f ~/lock_${NAME} ]; then
    exit 0
fi

date > ~/lock_${NAME}

DIR="/srv/builder/$NAME"
cd $DIR
git fetch -q

if [ $( git diff --name-only origin/${BRANCH:-master} | wc -l ) -eq 0 -a ! -f ~/git_updated_${NAME} ]; then
  rm -f ~/lock_${NAME}
  exit 0
fi

touch  ~/git_updated_${NAME}
git pull --rebase
bundle install
bundle exec middleman build > ~/error_${NAME} 2>&1

if [ $? -ne 0 ]; then
    if [ -n "$MAIL" ]; then
        echo "Build failed for $NAME" | EMAIL=nobody@redhat.com mutt -s "Build failed for $NAME" $EMAIL_ERROR -a ~/error_${NAME}
    fi
    rm -f ~/lock_${NAME}
    exit 1
fi

rm -f ~/error_${NAME}
if [[ ! -z $REMOTE ]] ; then
    rsync -e "ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -i $HOME/.ssh/${NAME}_id.rsa" -rqavz $DIR/build/ $REMOTE/
else
    bundle exec middleman deploy
fi;

date > ~/last_update_$NAME
rm -f ~/lock_${NAME} ~/git_updated_${NAME}
